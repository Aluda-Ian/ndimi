#!/usr/bin/env python3
"""Ndimi data check: take stock of a speech dataset and turn it into one clean manifest.

Point it at the folder you received (for example AfriVoices-KE). It finds the audio,
the transcripts and any translations, works out which clip belongs to which language,
dialect and speaker, measures every clip, and writes three files:

    out/inventory.md     the report to read: hours, speakers, gaps and problems per language
    out/inventory.json   the same numbers for scripts
    out/manifest.jsonl   one line per clip in a single standard format, with a
                         train/dev/test split that keeps each speaker in one split.
                         The training notebook reads this file.

Usage
    python ml/data_check.py /path/to/dataset
    python ml/data_check.py /path/to/dataset --out ml/out --mapping ml/mapping.yaml
    python ml/data_check.py /path/to/dataset --language luo      # whole folder is one language
    python ml/data_check.py /path/to/dataset --fast              # skip measuring audio

Needs only Python 3.9+. It uses soundfile, pandas/pyarrow (for .parquet) and ffprobe
when they are installed, and falls back to the standard library otherwise.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

AUDIO_EXTS = {'.wav', '.flac', '.mp3', '.ogg', '.opus', '.m4a', '.aac', '.webm', '.wma'}
TABLE_EXTS = {'.csv', '.tsv', '.json', '.jsonl', '.parquet'}

# Column names seen in common speech datasets (Common Voice, Hugging Face, FLEURS, custom CSVs).
# Override any of these with --mapping.
CANDIDATES = {
    'audio': ['audio', 'path', 'audio_path', 'audio_filepath', 'file', 'filename', 'file_name', 'wav',
              'wav_path', 'audio_file', 'clip', 'recording', 'utt_id', 'id'],
    'text': ['text', 'transcript', 'transcription', 'sentence', 'normalized_text', 'raw_transcription',
             'utterance', 'label', 'prompt'],
    'language': ['language', 'lang', 'language_code', 'lang_code', 'locale', 'lang_id'],
    'dialect': ['dialect', 'variety', 'accent', 'accents', 'sub_language', 'region'],
    'speaker': ['speaker', 'speaker_id', 'client_id', 'spk', 'spk_id', 'participant', 'participant_id', 'reader'],
    'gender': ['gender', 'sex'],
    'duration': ['duration', 'duration_s', 'duration_sec', 'length', 'secs', 'seconds'],
    'translation_en': ['translation', 'english', 'en', 'translation_en', 'english_translation', 'text_en'],
    'translation_sw': ['swahili', 'sw', 'kiswahili', 'translation_sw', 'swahili_translation', 'text_sw'],
    'split': ['split', 'subset', 'partition'],
}

# Language names and codes -> the codes Ndimi uses (projects.Language.code).
LANGUAGE_ALIASES = {
    'sw': ['sw', 'swa', 'swh', 'swahili', 'kiswahili'],
    'ki': ['ki', 'kik', 'kikuyu', 'gikuyu', 'gĩkũyũ'],
    'luo': ['luo', 'dholuo', 'dho luo'],
    'kln': ['kln', 'kalenjin', 'nandi', 'kipsigis'],
    'mas': ['mas', 'maa', 'maasai', 'masai'],
    'so': ['so', 'som', 'somali'],
    'luy': ['luy', 'luhya', 'luyia', 'oluluhya', 'bukusu', 'maragoli', 'idakho'],
    'mer': ['mer', 'meru', 'kimeru'],
    'kam': ['kam', 'kamba', 'kikamba'],
    'guz': ['guz', 'kisii', 'ekegusii', 'gusii'],
    'en': ['en', 'eng', 'english'],
}
ALIAS_TO_CODE = {alias: code for code, aliases in LANGUAGE_ALIASES.items() for alias in aliases}
LANGUAGE_NAMES = {'sw': 'Kiswahili', 'ki': 'Gĩkũyũ', 'luo': 'Dholuo', 'kln': 'Kalenjin', 'mas': 'Maa',
                  'so': 'Somali', 'luy': 'Oluluhya', 'mer': 'Kimeru', 'kam': 'Kikamba', 'guz': 'Ekegusii',
                  'en': 'English'}

# Thresholds used in the "ready for" verdicts. Rough guides, not hard rules.
READY = {
    'asr_start_hours': 5,        # enough to fine-tune and see an improvement
    'asr_good_hours': 20,        # enough to expect production quality
    'mt_pairs': 2000,            # sentence pairs for a first translation fine-tune
    'tts_single_speaker_hours': 1.0,
}
MIN_CLIP_S, MAX_CLIP_S = 0.5, 30.0     # Whisper-style models take up to 30 s per clip


# ---------------------------------------------------------------------------------------
# helpers

def norm_key(s):
    return re.sub(r'[^a-z0-9]+', '_', str(s).strip().lower()).strip('_')


def language_code(value):
    if value is None:
        return ''
    key = str(value).strip().lower()
    if not key:
        return ''
    key = key.split('-')[0].split('_')[0] if key not in ALIAS_TO_CODE else key
    return ALIAS_TO_CODE.get(key, ALIAS_TO_CODE.get(str(value).strip().lower(), str(value).strip().lower()))


def language_from_path(path: Path, root: Path):
    for part in path.relative_to(root).parts[:-1]:
        code = ALIAS_TO_CODE.get(part.strip().lower()) or ALIAS_TO_CODE.get(norm_key(part).replace('_', ' '))
        if code:
            return code
        for token in re.split(r'[^a-z]+', part.lower()):
            if token in ALIAS_TO_CODE and len(token) > 2:
                return ALIAS_TO_CODE[token]
    return ''


def clean_text(value):
    if value is None:
        return ''
    if isinstance(value, float) and value != value:  # NaN
        return ''
    return re.sub(r'\s+', ' ', str(value)).strip()


def stable_bucket(key: str) -> float:
    return int(hashlib.sha1(key.encode('utf-8')).hexdigest()[:8], 16) / 0xFFFFFFFF


def split_for(speaker, audio, dev=0.1, test=0.1):
    """Speaker-disjoint split: every clip of a speaker lands in the same split."""
    b = stable_bucket(speaker or audio)
    return 'test' if b < test else 'dev' if b < test + dev else 'train'


# ---------------------------------------------------------------------------------------
# audio measurement

try:
    import soundfile as _sf  # type: ignore
except Exception:  # noqa: BLE001
    _sf = None
FFPROBE = shutil.which('ffprobe')


def probe_audio(path=None, data: bytes | None = None):
    """Return (duration_s, sample_rate, channels, error)."""
    try:
        if _sf is not None:
            info = _sf.info(io.BytesIO(data) if data is not None else str(path))
            return float(info.duration), int(info.samplerate), int(info.channels), ''
    except Exception:  # noqa: BLE001 - fall through to other readers
        pass
    try:
        if data is not None or str(path).lower().endswith('.wav'):
            with wave.open(io.BytesIO(data) if data is not None else str(path), 'rb') as w:
                return w.getnframes() / float(w.getframerate()), w.getframerate(), w.getnchannels(), ''
    except Exception:  # noqa: BLE001
        pass
    if FFPROBE and path is not None:
        try:
            out = subprocess.run(
                [FFPROBE, '-v', 'error', '-select_streams', 'a:0', '-show_entries',
                 'stream=sample_rate,channels:format=duration', '-of', 'json', str(path)],
                capture_output=True, text=True, timeout=60)
            j = json.loads(out.stdout or '{}')
            stream = (j.get('streams') or [{}])[0]
            dur = float(j.get('format', {}).get('duration') or 0)
            return dur, int(stream.get('sample_rate') or 0), int(stream.get('channels') or 0), ''
        except Exception as e:  # noqa: BLE001
            return None, None, None, f'unreadable: {e}'
    return None, None, None, 'unreadable (install soundfile or ffmpeg to read this format)'


# ---------------------------------------------------------------------------------------
# reading metadata tables

def read_table(path: Path):
    ext = path.suffix.lower()
    if ext in ('.csv', '.tsv'):
        with open(path, newline='', encoding='utf-8-sig', errors='replace') as fh:
            sample = fh.read(8192)
            fh.seek(0)
            delim = '\t' if ext == '.tsv' else (csv.Sniffer().sniff(sample, delimiters=',;\t|').delimiter
                                               if sample else ',')
            return list(csv.DictReader(fh, delimiter=delim))
    if ext == '.jsonl':
        rows = []
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return rows
    if ext == '.json':
        with open(path, encoding='utf-8', errors='replace') as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for key in ('data', 'rows', 'items', 'utterances', 'clips', 'records'):
                if isinstance(data.get(key), list):
                    return data[key]
            if all(isinstance(v, dict) for v in data.values()):  # {"clip_id": {...}}
                return [{'id': k, **v} for k, v in data.items()]
            return []
        return data if isinstance(data, list) else []
    if ext == '.parquet':
        try:
            import pandas as pd  # type: ignore
            return pd.read_parquet(path).to_dict('records')
        except Exception as e:  # noqa: BLE001
            print(f'  ! Skipping {path.name}: install pandas + pyarrow to read parquet ({e})', file=sys.stderr)
            return []
    return []


def pick_columns(columns, mapping):
    keys = {norm_key(c): c for c in columns}
    picked = {}
    for field, cands in CANDIDATES.items():
        if mapping.get('columns', {}).get(field):
            want = mapping['columns'][field]
            picked[field] = keys.get(norm_key(want), want if want in columns else None)
            continue
        for cand in cands:
            if norm_key(cand) in keys:
                picked[field] = keys[norm_key(cand)]
                break
    # Don't use the same column for audio and text.
    if picked.get('audio') and picked.get('audio') == picked.get('text'):
        picked.pop('text')
    return picked


def looks_like_audio_ref(v):
    if isinstance(v, dict):
        return 'bytes' in v or 'path' in v
    return isinstance(v, str) and Path(v).suffix.lower() in AUDIO_EXTS


# ---------------------------------------------------------------------------------------
# main scan

class Scan:
    def __init__(self, root: Path, out: Path, mapping: dict, forced_language: str, fast: bool, workers: int):
        self.root, self.out, self.mapping, self.forced = root, out, mapping, forced_language
        self.fast, self.workers = fast, workers
        self.audio_files: list[Path] = []
        self.by_name: dict[str, list[Path]] = collections.defaultdict(list)
        self.tables: list[Path] = []
        self.text_sidecars: dict[Path, Path] = {}
        self.records: dict[str, dict] = {}       # key: resolved audio path or embedded id
        self.notes: list[str] = []

    def discover(self):
        skip_dirs = {'.git', '__pycache__', '.venv', 'node_modules'}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs and Path(dirpath, d).resolve() != self.out.resolve()]
            for name in filenames:
                p = Path(dirpath, name)
                ext = p.suffix.lower()
                if ext in AUDIO_EXTS:
                    self.audio_files.append(p)
                    self.by_name[p.name.lower()].append(p)
                    self.by_name[p.stem.lower()].append(p)
                elif ext in TABLE_EXTS and name.lower() not in {'package.json', 'inventory.json'}:
                    self.tables.append(p)
                elif ext == '.txt':
                    self.text_sidecars[p.with_suffix('')] = p
        print(f'Found {len(self.audio_files):,} audio files and {len(self.tables)} metadata files.')

    def resolve_audio(self, ref, table_path: Path):
        if isinstance(ref, dict):
            return None
        ref = str(ref).strip().replace('\\', '/')
        if not ref:
            return None
        for base in (table_path.parent, self.root, table_path.parent / 'clips', table_path.parent / 'audio',
                     table_path.parent / 'wav', self.root / 'clips', self.root / 'audio'):
            cand = (base / ref)
            if cand.suffix == '':
                for ext in AUDIO_EXTS:
                    if cand.with_suffix(ext).exists():
                        return cand.with_suffix(ext)
            if cand.exists():
                return cand
        hits = self.by_name.get(Path(ref).name.lower()) or self.by_name.get(Path(ref).stem.lower())
        if hits:
            if len(hits) > 1:
                same_dir = [h for h in hits if h.parent == table_path.parent]
                return (same_dir or hits)[0]
            return hits[0]
        return None

    def add(self, key, **fields):
        rec = self.records.get(key)
        if rec is None:
            rec = self.records[key] = {'audio': fields.get('audio'), 'sources': []}
        for k, v in fields.items():
            if v not in (None, '') and not rec.get(k):
                rec[k] = v
        return rec

    def read_tables(self):
        embedded_dir = self.out / 'audio_embedded'
        for table in sorted(self.tables):
            try:
                rows = read_table(table)
            except Exception as e:  # noqa: BLE001
                self.notes.append(f'Could not read {table.relative_to(self.root)}: {e}')
                continue
            if not rows or not isinstance(rows[0], dict):
                continue
            cols = pick_columns(list(rows[0].keys()), self.mapping)
            audio_col = cols.get('audio')
            if not audio_col or not any(looks_like_audio_ref(r.get(audio_col)) or r.get(audio_col) for r in rows[:50]):
                # Maybe the audio column has an unusual name: find one whose values look like audio files.
                for c in rows[0].keys():
                    if sum(looks_like_audio_ref(r.get(c)) for r in rows[:50]) >= 1:
                        audio_col = c
                        break
            if not audio_col:
                self.notes.append(f'{table.relative_to(self.root)}: no audio column found, skipped '
                                  f'(columns: {", ".join(map(str, rows[0].keys()))[:200]}). Set it in --mapping.')
                continue
            used = {k: v for k, v in cols.items() if v}
            print(f'  {table.relative_to(self.root)}: {len(rows):,} rows; columns '
                  + ', '.join(f'{k}={v}' for k, v in used.items()))
            table_lang = self.forced or language_code(self.mapping.get('language')) or language_from_path(table, self.root)
            for i, row in enumerate(rows):
                ref = row.get(audio_col)
                audio_path, embedded = None, None
                if isinstance(ref, dict):  # Hugging Face "Audio" feature: {'bytes': ..., 'path': ...}
                    if ref.get('bytes'):
                        embedded = ref['bytes']
                    elif ref.get('path'):
                        audio_path = self.resolve_audio(ref['path'], table)
                elif ref not in (None, ''):
                    audio_path = self.resolve_audio(ref, table)
                if embedded is not None:
                    name = Path(str(ref.get('path') or f'{table.stem}_{i}')).name
                    if Path(name).suffix.lower() not in AUDIO_EXTS:
                        name += '.wav'
                    embedded_dir.mkdir(parents=True, exist_ok=True)
                    target = embedded_dir / f'{hashlib.sha1(embedded).hexdigest()[:10]}_{name}'
                    if not target.exists():
                        target.write_bytes(embedded)
                    audio_path = target
                key = str(audio_path.resolve()) if audio_path else f'missing::{table.name}::{ref}'
                lang = language_code(row.get(cols['language'])) if cols.get('language') else ''
                rec = self.add(
                    key,
                    audio=str(audio_path) if audio_path else None,
                    audio_ref=None if audio_path else str(ref),
                    text=clean_text(row.get(cols['text'])) if cols.get('text') else '',
                    language=self.forced or lang or table_lang,
                    dialect=clean_text(row.get(cols['dialect'])) if cols.get('dialect') else '',
                    speaker=clean_text(row.get(cols['speaker'])) if cols.get('speaker') else '',
                    gender=clean_text(row.get(cols['gender'])).lower() if cols.get('gender') else '',
                    translation_en=clean_text(row.get(cols['translation_en'])) if cols.get('translation_en') else '',
                    translation_sw=clean_text(row.get(cols['translation_sw'])) if cols.get('translation_sw') else '',
                    listed_duration=_to_float(row.get(cols['duration'])) if cols.get('duration') else None,
                    source_split=clean_text(row.get(cols['split'])).lower() if cols.get('split') else '',
                )
                rec['sources'].append(str(table.relative_to(self.root)) if table.is_relative_to(self.root) else table.name)

    def add_unlisted_audio(self):
        for p in self.audio_files:
            key = str(p.resolve())
            sidecar = self.text_sidecars.get(p.with_suffix(''))
            text = ''
            if sidecar:
                try:
                    text = clean_text(sidecar.read_text(encoding='utf-8', errors='replace'))
                except OSError:
                    pass
            rec = self.add(key, audio=str(p), text=text,
                           language=self.forced or language_code(self.mapping.get('language')) or language_from_path(p, self.root))
            if sidecar and 'sidecar .txt' not in rec['sources']:
                rec['sources'].append('sidecar .txt')

    def measure(self):
        todo = [r for r in self.records.values() if r.get('audio')]
        if self.fast:
            for r in todo:
                r['duration'] = r.get('listed_duration')
            print('Skipped measuring audio (--fast); durations come from the metadata where listed.')
            return
        print(f'Measuring {len(todo):,} clips…')

        def work(r):
            d, sr, ch, err = probe_audio(r['audio'])
            r['duration'], r['sample_rate'], r['channels'] = d, sr, ch
            if err:
                r['read_error'] = err

        done = 0
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for _ in pool.map(work, todo):
                done += 1
                if done % 2000 == 0:
                    print(f'  {done:,}/{len(todo):,}')

    def finalize(self):
        seen_audio_hash = {}
        for r in self.records.values():
            issues = []
            if not r.get('audio'):
                issues.append('audio_missing')
            if r.get('read_error'):
                issues.append('audio_unreadable')
            d = r.get('duration')
            if d is not None:
                if d < MIN_CLIP_S:
                    issues.append('too_short')
                elif d > MAX_CLIP_S:
                    issues.append('too_long')
            text = r.get('text') or ''
            if not text:
                issues.append('no_transcript')
            elif d:
                cps = len(text) / d
                if cps > 30:
                    issues.append('text_too_long_for_audio')
                elif cps < 1.5 and d > 3:
                    issues.append('text_too_short_for_audio')
            if not r.get('language'):
                issues.append('language_unknown')
            if r.get('sample_rate') and r['sample_rate'] < 16000:
                issues.append('low_sample_rate')
            if r.get('audio') and r.get('duration'):
                sig = (round(r['duration'], 3), r.get('text'), r.get('speaker'))
                if sig in seen_audio_hash and text:
                    issues.append('possible_duplicate')
                seen_audio_hash.setdefault(sig, r['audio'])
            r['issues'] = issues
            r['usable_for_asr'] = not ({'audio_missing', 'audio_unreadable', 'too_short', 'too_long', 'no_transcript',
                                         'text_too_long_for_audio', 'language_unknown', 'possible_duplicate'} & set(issues))
            src = r.get('source_split')
            r['split'] = {'validation': 'dev', 'valid': 'dev', 'val': 'dev', 'eval': 'dev'}.get(src, src) \
                if src in ('train', 'dev', 'test', 'validation', 'valid', 'val', 'eval') \
                else split_for(r.get('speaker'), r.get('audio') or '')

    # -----------------------------------------------------------------------------------
    # outputs

    def write(self):
        self.out.mkdir(parents=True, exist_ok=True)
        rows = sorted(self.records.values(), key=lambda r: (r.get('language') or '~', r.get('audio') or ''))
        with open(self.out / 'manifest.jsonl', 'w', encoding='utf-8') as fh:
            for r in rows:
                audio = r.get('audio')
                rel = None
                if audio:
                    try:
                        rel = str(Path(audio).resolve().relative_to(self.root.resolve())).replace('\\', '/')
                    except ValueError:
                        rel = str(Path(audio).resolve()).replace('\\', '/')
                fh.write(json.dumps({
                    'audio': rel, 'duration': _round(r.get('duration')), 'text': r.get('text') or '',
                    'language': r.get('language') or '', 'dialect': r.get('dialect') or '',
                    'speaker': r.get('speaker') or '', 'gender': r.get('gender') or '',
                    'translation_en': r.get('translation_en') or '', 'translation_sw': r.get('translation_sw') or '',
                    'sample_rate': r.get('sample_rate'), 'split': r['split'],
                    'usable_for_asr': r['usable_for_asr'], 'issues': r['issues'],
                }, ensure_ascii=False) + '\n')

        inv = self.inventory(rows)
        (self.out / 'inventory.json').write_text(json.dumps(inv, indent=2, ensure_ascii=False), encoding='utf-8')
        (self.out / 'inventory.md').write_text(self.report(inv), encoding='utf-8')
        print(f'\nWrote {self.out / "inventory.md"}, inventory.json and manifest.jsonl ({len(rows):,} clips).')

    def inventory(self, rows):
        langs = collections.defaultdict(list)
        for r in rows:
            langs[r.get('language') or 'unknown'].append(r)
        out = {'dataset': str(self.root), 'clips': len(rows), 'notes': self.notes, 'languages': {}}
        for code, rs in sorted(langs.items(), key=lambda kv: -sum(x.get('duration') or 0 for x in kv[1])):
            hours = lambda sel: round(sum((x.get('duration') or 0) for x in sel) / 3600, 2)  # noqa: E731
            usable = [x for x in rs if x['usable_for_asr']]
            speakers = collections.Counter(x.get('speaker') or '(unknown)' for x in rs)
            spk_hours = collections.defaultdict(float)
            for x in usable:
                spk_hours[x.get('speaker') or '(unknown)'] += (x.get('duration') or 0) / 3600
            top_speaker = max(spk_hours.items(), key=lambda kv: kv[1]) if spk_hours else ('', 0)
            dialects = collections.defaultdict(list)
            for x in rs:
                dialects[x.get('dialect') or '(not labelled)'].append(x)
            issues = collections.Counter(i for x in rs for i in x['issues'])
            pairs_en = sum(1 for x in rs if x.get('text') and x.get('translation_en'))
            pairs_sw = sum(1 for x in rs if x.get('text') and x.get('translation_sw'))
            durations = [x['duration'] for x in rs if x.get('duration')]
            splits = collections.Counter(x['split'] for x in usable)
            asr_h = hours(usable)
            verdict = {
                'transcription': ('ready' if asr_h >= READY['asr_good_hours'] else
                                  'start (fine-tune and compare)' if asr_h >= READY['asr_start_hours'] else
                                  'not enough yet'),
                'translation': ('start' if max(pairs_en, pairs_sw) >= READY['mt_pairs'] else
                                'not enough yet' if max(pairs_en, pairs_sw) else 'no translations in this data'),
                'voice': ('candidate' if top_speaker[1] >= READY['tts_single_speaker_hours'] else 'not enough single-speaker audio'),
            }
            out['languages'][code] = {
                'name': LANGUAGE_NAMES.get(code, code),
                'clips': len(rs), 'hours_total': hours(rs),
                'clips_transcribed': sum(1 for x in rs if x.get('text')),
                'hours_usable_for_transcription': asr_h,
                'speakers': len([s for s in speakers if s != '(unknown)']),
                'speaker_unknown_clips': speakers.get('(unknown)', 0),
                'top_speaker': {'id': top_speaker[0], 'hours': round(top_speaker[1], 2)},
                'gender': dict(collections.Counter(x.get('gender') or 'unknown' for x in rs)),
                'dialects': {d: {'clips': len(v), 'hours': hours(v)} for d, v in
                             sorted(dialects.items(), key=lambda kv: -len(kv[1]))},
                'sentence_pairs': {'english': pairs_en, 'kiswahili': pairs_sw},
                'clip_seconds': ({'min': round(min(durations), 1), 'median': round(statistics.median(durations), 1),
                                  'max': round(max(durations), 1)} if durations else None),
                'sample_rates': dict(collections.Counter(str(x.get('sample_rate') or '?') for x in rs)),
                'usable_split_hours': {s: hours([x for x in usable if x['split'] == s]) for s in ('train', 'dev', 'test')},
                'usable_split_clips': dict(splits),
                'issues': dict(issues.most_common()),
                'ready_for': verdict,
            }
        return out

    def report(self, inv):
        L = [f'# Dataset inventory', '', f'Folder: `{inv["dataset"]}`  ', f'Clips: {inv["clips"]:,}', '']
        L += ['## Summary', '', '| Language | Hours | Usable for transcription (h) | Speakers | Sentence pairs (EN / SW) | Transcription | Translation | Voice |',
              '|---|---:|---:|---:|---:|---|---|---|']
        for code, d in inv['languages'].items():
            L.append(f'| {d["name"]} (`{code}`) | {d["hours_total"]} | {d["hours_usable_for_transcription"]} | '
                     f'{d["speakers"]} | {d["sentence_pairs"]["english"]:,} / {d["sentence_pairs"]["kiswahili"]:,} | '
                     f'{d["ready_for"]["transcription"]} | {d["ready_for"]["translation"]} | {d["ready_for"]["voice"]} |')
        L += ['', f'Thresholds: transcription starts at {READY["asr_start_hours"]} h and is production-ready around '
              f'{READY["asr_good_hours"]} h of clean transcribed speech; translation needs about {READY["mt_pairs"]:,} '
              f'sentence pairs; a voice needs about {READY["tts_single_speaker_hours"]:g} h from one clear speaker.', '']
        for code, d in inv['languages'].items():
            L += [f'## {d["name"]} (`{code}`)', '',
                  f'- {d["clips"]:,} clips, {d["hours_total"]} h; {d["clips_transcribed"]:,} transcribed',
                  f'- Usable for transcription training: **{d["hours_usable_for_transcription"]} h** '
                  f'(train {d["usable_split_hours"]["train"]} h / dev {d["usable_split_hours"]["dev"]} h / test {d["usable_split_hours"]["test"]} h, split by speaker)',
                  f'- Speakers: {d["speakers"]:,}' + (f' ({d["speaker_unknown_clips"]:,} clips have no speaker id)' if d['speaker_unknown_clips'] else ''),
                  f'- Most-recorded speaker: {d["top_speaker"]["id"] or "n/a"} ({d["top_speaker"]["hours"]} h)',
                  f'- Gender: ' + ', '.join(f'{k} {v:,}' for k, v in d['gender'].items()),
                  f'- Clip length (s): ' + (f'min {d["clip_seconds"]["min"]}, median {d["clip_seconds"]["median"]}, max {d["clip_seconds"]["max"]}' if d['clip_seconds'] else 'not measured'),
                  f'- Sample rates: ' + ', '.join(f'{k} Hz × {v:,}' for k, v in d['sample_rates'].items()), '',
                  '| Dialect | Clips | Hours |', '|---|---:|---:|']
            L += [f'| {name} | {v["clips"]:,} | {v["hours"]} |' for name, v in d['dialects'].items()]
            if d['issues']:
                L += ['', 'Problems found:', '']
                L += [f'- {ISSUE_TEXT.get(k, k)}: {v:,}' for k, v in d['issues'].items()]
            L.append('')
        if inv['notes']:
            L += ['## Notes', ''] + [f'- {n}' for n in inv['notes']] + ['']
        L += ['## Next step', '',
              'Pick the language with the most usable transcription hours and open `ml/train_asr.ipynb`. '
              'It reads `manifest.jsonl` from this folder.', '']
        return '\n'.join(L)


ISSUE_TEXT = {
    'no_transcript': 'No transcript',
    'audio_missing': 'Listed in metadata but the audio file is missing',
    'audio_unreadable': 'Audio could not be read',
    'too_short': f'Shorter than {MIN_CLIP_S} s',
    'too_long': f'Longer than {MAX_CLIP_S:g} s (needs splitting before training)',
    'text_too_long_for_audio': 'Transcript far too long for the clip (likely misaligned)',
    'text_too_short_for_audio': 'Transcript very short for the clip (possibly partial)',
    'language_unknown': 'Language not identified (set --language or a language column)',
    'low_sample_rate': 'Sample rate below 16 kHz',
    'possible_duplicate': 'Possible duplicate',
}


def _to_float(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _round(v):
    return round(v, 3) if isinstance(v, (int, float)) else None


def load_mapping(path):
    if not path:
        return {}
    text = Path(path).read_text(encoding='utf-8')
    if path.endswith(('.yaml', '.yml')):
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    return json.loads(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dataset', help='Folder that holds the dataset')
    ap.add_argument('--out', default=None, help='Where to write the report and manifest (default: ml/out/<dataset name>)')
    ap.add_argument('--mapping', help='YAML/JSON file naming columns or the language, see ml/mapping.example.yaml')
    ap.add_argument('--language', help='Treat every clip as this language (e.g. luo, kln, mas)')
    ap.add_argument('--fast', action='store_true', help="Don't open audio files; use durations from the metadata")
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args(argv)

    root = Path(args.dataset).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f'{root} is not a folder.')
    out = Path(args.out) if args.out else Path(__file__).resolve().parent / 'out' / norm_key(root.name)
    scan = Scan(root, out, load_mapping(args.mapping), language_code(args.language) if args.language else '',
                args.fast, args.workers)
    scan.discover()
    scan.read_tables()
    scan.add_unlisted_audio()
    scan.measure()
    scan.finalize()
    scan.write()


if __name__ == '__main__':
    main()
