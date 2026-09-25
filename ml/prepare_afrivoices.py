#!/usr/bin/env python3
"""Turn one downloaded AfriVoices-KE language repo into WAV files + one metadata.csv.

The Hugging Face repos (Anv-ke/kikuyu, Anv-ke/Dholuo, ...) are laid out like this:

    <repo>/
      train/ dev/ dev_test/
        scripted/ unscripted/
          audios/*.parquet        audio stored inside, plus filename, recorder_uuid, split, type...
          files/transcripts.csv   the transcripts, one row per clip, joined on the file name
          files/meta.csv          one row per speaker (recorder_uuid): dialect, gender, county...

data_check.py can't join audio held in parquet with transcripts held in a separate CSV,
so this script does the join once and writes a layout data_check understands.

What the real files hold (checked on Anv-ke/kikuyu, Sept 2026):
    transcripts.csv  mediaPathId (VOICE_COLLECTION/<file>.wav), recorder_uuid, domain,
                     translatedText (the source prompt, mostly Kiswahili, some English),
                     actualSentence (the prompt in the local language, which the speaker read),
                     duration, sentenceSource, language, sentenceDialect (often blank), type
    meta.csv         recorder_uuid, language, dialect, educationLevel, employmentState,
                     countyName, constituencyName, ownerAge, gender
For scripted speech the text we train on is actualSentence. Speaker gender and a missing
dialect come from meta.csv. Only gender and dialect are copied from meta.csv; education,
employment, age and location stay out of our files.

    <out>/
      audio/<split>/<type>/<filename>.wav
      metadata.csv                audio, text, language, dialect, speaker, gender, duration,
                                  translation_en, translation_sw, split, type, source
      prepare_report.txt          what was matched, what wasn't, which columns were used

Usage (Windows, from the project folder):
    pip install pyarrow
    python ml\\prepare_afrivoices.py "G:\\Datasets\\afrivoices-ke\\kikuyu" --out "G:\\Datasets\\afrivoices-ke\\kikuyu-prepared"
    python ml\\data_check.py "G:\\Datasets\\afrivoices-ke\\kikuyu-prepared" --language ki --out "G:\\Datasets\\afrivoices-ke\\kikuyu-prepared\\_ndimi"

Options:
    --splits train,dev,dev_test   which splits to prepare (default: all found)
    --types scripted,unscripted   which recording types (default: all found)
    --max-files-per-group N       only the first N parquet files per split/type (quick trial)
    --text-col / --key-col / --speaker-col / --dialect-col ...   force a column when auto-detection picks the wrong one

It is safe to stop and re-run: WAV files that already exist are not written again.
The official split names are kept, except dev_test, which becomes "test" for data_check.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import unicodedata
import wave
from pathlib import Path

AUDIO_EXTS = ('.wav', '.flac', '.mp3', '.ogg', '.opus', '.m4a', '.webm')
SPLIT_NAMES = {'train': 'train', 'dev': 'dev', 'dev_test': 'test', 'devtest': 'test', 'test': 'test'}

# Column candidates, most likely first. Compared after lowercasing and removing _ and spaces.
CANDIDATES = {
    'key': ['filename', 'file_name', 'file', 'audio_filename', 'mediapathid', 'audio_path', 'path', 'audio', 'id', 'utt_id'],
    'text': ['transcript', 'transcription', 'text', 'normalizedtranscript', 'normalized_text', 'sentence', 'actualsentence'],
    'speaker': ['recorder_uuid', 'recorderuuid', 'speaker_id', 'speaker', 'participant_id', 'client_id'],
    'dialect': ['sentencedialect', 'dialect', 'recorderdialect', 'variety', 'accent'],
    'gender': ['gender', 'sex', 'recordergender'],
    'duration': ['duration', 'duration_s', 'duration_sec', 'audio_duration', 'length'],
    'translation_en': ['translatedtext', 'translation', 'translation_en', 'english', 'en'],
    'translation_sw': ['translation_sw', 'swahili', 'kiswahili', 'sw'],
}

LANGUAGE_FROM_NAME = {'kikuyu': 'ki', 'gikuyu': 'ki', 'kik': 'ki', 'dholuo': 'luo', 'luo': 'luo',
                      'kalenjin': 'kln', 'kln': 'kln', 'maasai': 'mas', 'maa': 'mas', 'mas': 'mas',
                      'somali': 'so', 'som': 'so', 'swahili': 'sw', 'kiswahili': 'sw'}


def squash(name):
    return re.sub(r'[\s_\-]+', '', str(name).strip().lower())


def clip_key(value):
    """File names are compared without folders, case or audio extension."""
    name = str(value or '').strip().replace('\\', '/').rsplit('/', 1)[-1].lower()
    for ext in AUDIO_EXTS:
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def pick(columns, field, forced=None):
    if forced:
        if forced not in columns:
            sys.exit(f'Column "{forced}" not found. Available: {", ".join(columns)}')
        return forced
    by_squash = {squash(c): c for c in columns}
    for cand in CANDIDATES[field]:
        if squash(cand) in by_squash:
            return by_squash[squash(cand)]
    return None


SW_WORDS = set('na ya wa kwa ni za la katika kuwa hii huo hiyo cha pia au watu kama lakini baada '
               'sana hilo kwamba yake wao hao mimi wewe sisi hapa kila ili bila zaidi hata'.split())
EN_WORDS = set('the of and to in is for that on with are was by as be this from it an at have has '
               'will not their they you we'.split())


def source_language(text):
    """The prompts were translated from Kiswahili or English; tell which, cheaply."""
    words = re.findall(r"[a-z']+", text.lower())
    en = sum(w in EN_WORDS for w in words)
    sw = sum(w in SW_WORDS for w in words)
    return 'en' if en >= 2 and en > sw else 'sw'


def looks_garbled(text):
    """Machine-translated prompts sometimes loop ("a airĩtu a airĩtu a airĩtu ...")."""
    words = re.findall(r'\w+', text.lower())
    return len(words) >= 12 and len(set(words)) / len(words) < 0.35


def tidy_dialect(value):
    """'GĨ-KABETE_(KIAMBU)', 'GĨ-KABETE(KIAMBU)', 'GĨ-_KABETE_(KIAMBU)' -> 'GĨ-KABETE (KIAMBU)'."""
    v = clean(value).replace('_', ' ')
    v = re.sub(r'\s*-\s*', '-', v)
    v = re.sub(r'\s*\(\s*', ' (', v)
    v = re.sub(r'\s*&\s*', ' & ', v)
    v = re.sub(r'\s+', ' ', v).strip()
    v = re.sub(r'^GĨ-?KABETE', 'GĨ-KABETE', v)
    return v


def tidy_gender(value):
    v = clean(value).lower()
    return {'f': 'female', 'm': 'male'}.get(v, v) if v in ('female', 'male', 'f', 'm', 'other') else ''


def clean(value):
    if value is None:
        return ''
    if isinstance(value, float) and value != value:
        return ''
    # NFC so that 'Ĩ' typed as one character and as I + combining tilde compare equal
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', str(value))).strip()


def read_csv(path: Path):
    with open(path, newline='', encoding='utf-8-sig', errors='replace') as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=',;\t|').delimiter
        except csv.Error:
            delim = ','
        return list(csv.DictReader(fh, delimiter=delim))


def iter_parquet_rows(path: Path, batch_size=64):
    """Yield one dict per row without loading a whole 750 MB file into memory."""
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        sys.exit('This needs pyarrow to read the audio files:  pip install pyarrow')
    pf = pq.ParquetFile(path)
    for batch in pf.iter_batches(batch_size=batch_size):
        yield from batch.to_pylist()


def wav_duration(data: bytes):
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:  # noqa: BLE001  (not a WAV, or empty)
        return None


def find_groups(root: Path, splits, types):
    groups = []
    for audios in sorted(root.glob('*/*/audios')):
        split_dir, type_dir = audios.parent.parent.name, audios.parent.name
        if splits and split_dir not in splits:
            continue
        if types and type_dir not in types:
            continue
        groups.append((split_dir, type_dir, audios, audios.parent / 'files'))
    return groups


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('repo', help='Downloaded language folder, e.g. G:\\Datasets\\afrivoices-ke\\kikuyu')
    ap.add_argument('--out', help='Where to write audio/ and metadata.csv (default: <repo>-prepared)')
    ap.add_argument('--language', help='Ndimi code (ki, luo, kln, mas, so). Default: from the folder name')
    ap.add_argument('--splits', help='Comma list, e.g. train,dev,dev_test')
    ap.add_argument('--types', help='Comma list: scripted,unscripted')
    ap.add_argument('--max-files-per-group', type=int, default=0)
    for field in ('key', 'text', 'speaker', 'dialect', 'gender', 'duration', 'translation_en', 'translation_sw'):
        ap.add_argument(f'--{field.replace("_", "-")}-col', dest=f'{field}_col')
    args = ap.parse_args(argv)

    root = Path(args.repo).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f'{root} is not a folder.')
    out = Path(args.out).expanduser().resolve() if args.out else root.with_name(root.name + '-prepared')
    language = args.language or LANGUAGE_FROM_NAME.get(root.name.lower(), '')
    if not language:
        sys.exit('Could not tell the language from the folder name; pass --language (ki, luo, kln, mas, so).')
    split_filter = set(args.splits.split(',')) if args.splits else None
    type_filter = set(args.types.split(',')) if args.types else None

    groups = find_groups(root, split_filter, type_filter)
    if not groups:
        sys.exit(f'No <split>/<type>/audios folders found under {root}. Is this a downloaded Anv-ke repo?')

    out.mkdir(parents=True, exist_ok=True)
    report = [f'Source: {root}', f'Language: {language}', '']
    fields = ['audio', 'text', 'language', 'dialect', 'speaker', 'gender', 'duration',
              'translation_en', 'translation_sw', 'split', 'type', 'source']
    totals = {'clips': 0, 'with_text': 0, 'written': 0, 'no_audio': 0, 'unmatched_transcripts': 0,
              'garbled_text': 0}

    with open(out / 'metadata.csv', 'w', newline='', encoding='utf-8') as meta_fh:
        writer = csv.DictWriter(meta_fh, fieldnames=fields)
        writer.writeheader()

        for split_dir, type_dir, audios, files in groups:
            split = SPLIT_NAMES.get(split_dir, split_dir)
            label = f'{split_dir}/{type_dir}'
            print(f'\n== {label}')

            # 1. Transcripts for this group, indexed by file name.
            transcripts, tcols = {}, {}
            tpath = files / 'transcripts.csv'
            if tpath.exists():
                rows = read_csv(tpath)
                cols = list(rows[0].keys()) if rows else []
                tcols = {f: pick(cols, f, getattr(args, f'{f}_col')) for f in CANDIDATES}
                if not tcols['key']:
                    sys.exit(f'{tpath}: no file-name column found (columns: {", ".join(cols)}). Use --key-col.')
                for r in rows:
                    transcripts[clip_key(r.get(tcols['key']))] = r
                used = ', '.join(f'{k}={v}' for k, v in tcols.items() if v)
                print(f'  transcripts.csv: {len(rows):,} rows; columns used: {used}')
                report += [f'[{label}] transcripts.csv columns: {", ".join(cols)}', f'[{label}] used: {used}']
            else:
                print('  no files/transcripts.csv (audio only)')
                report.append(f'[{label}] no transcripts.csv')
            speakers = {}
            if (files / 'meta.csv').exists():
                mrows = read_csv(files / 'meta.csv')
                mcols = list(mrows[0].keys()) if mrows else []
                mkey = pick(mcols, 'speaker', None)
                mdialect, mgender = pick(mcols, 'dialect', None), pick(mcols, 'gender', None)
                if mkey:
                    for r in mrows:
                        speakers[clean(r.get(mkey))] = {
                            'dialect': tidy_dialect(r.get(mdialect)) if mdialect else '',
                            'gender': tidy_gender(r.get(mgender)) if mgender else ''}
                report.append(f'[{label}] meta.csv: {len(speakers):,} speakers; columns: {", ".join(mcols)}')

            # 2. Audio from the parquet files.
            parquets = sorted(audios.glob('*.parquet'))
            if args.max_files_per_group:
                parquets = parquets[: args.max_files_per_group]
            target_dir = out / 'audio' / split_dir / type_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            seen, pcols = set(), None
            for pfile in parquets:
                n = 0
                for row in iter_parquet_rows(pfile):
                    if pcols is None:
                        cols = [c for c in row.keys()]
                        pcols = {f: pick(cols, f, None) for f in CANDIDATES}
                        audio_col = next((c for c in cols if isinstance(row[c], dict) and 'bytes' in row[c]), None)
                        pcols['audio'] = audio_col
                        report.append(f'[{label}] parquet columns: {", ".join(cols)}')
                        if not audio_col:
                            sys.exit(f'{pfile}: no audio column found (columns: {", ".join(cols)}).')
                    audio = row.get(pcols['audio']) or {}
                    name_src = row.get(pcols['key']) if pcols.get('key') and pcols['key'] != pcols['audio'] else None
                    name = clip_key(name_src or audio.get('path') or f'{pfile.stem}_{n}')
                    n += 1
                    totals['clips'] += 1
                    data = audio.get('bytes')
                    if not data:
                        totals['no_audio'] += 1
                        continue
                    target = target_dir / f'{name}.wav'
                    if not target.exists():
                        tmp = target.with_suffix('.part')
                        tmp.write_bytes(data)
                        tmp.replace(target)
                        totals['written'] += 1
                    seen.add(name)

                    t = transcripts.get(name, {})

                    def val(field):
                        # transcript CSV first, then the parquet row
                        if t and tcols.get(field):
                            v = clean(t.get(tcols[field]))
                            if v:
                                return v
                        return clean(row.get(pcols[field])) if pcols.get(field) else ''

                    text = val('text')
                    if text and looks_garbled(text):
                        totals['garbled_text'] += 1
                        text = ''          # keep the audio, but don't train speech-to-text on it
                    totals['with_text'] += bool(text)
                    speaker = val('speaker')
                    who = speakers.get(speaker, {})
                    tr_en, tr_sw = val('translation_en'), val('translation_sw')
                    if tr_en and not tr_sw and tcols.get('translation_en') and squash(tcols['translation_en']) == 'translatedtext':
                        # AfriVoices "translatedText" is the source prompt: Kiswahili or English
                        if source_language(tr_en) == 'sw':
                            tr_en, tr_sw = '', tr_en
                    writer.writerow({
                        'audio': target.relative_to(out).as_posix(), 'text': text, 'language': language,
                        'dialect': tidy_dialect(val('dialect')) or who.get('dialect', ''),
                        'speaker': speaker, 'gender': tidy_gender(val('gender')) or who.get('gender', ''),
                        'duration': val('duration') or (round(wav_duration(data) or 0, 3) or ''),
                        'translation_en': tr_en, 'translation_sw': tr_sw,
                        'split': split, 'type': type_dir, 'source': f'{label}/{pfile.name}',
                    })
                print(f'  {pfile.name}: {n:,} clips')
            missing = len(set(transcripts) - seen)
            if not args.max_files_per_group:
                totals['unmatched_transcripts'] += missing
            report.append(f'[{label}] clips {len(seen):,}; transcripts without usable audio: {missing:,}'
                          + (' (partial run)' if args.max_files_per_group else ''))
            report.append('')

    summary = (f'Clips: {totals["clips"]:,}  with transcript: {totals["with_text"]:,}  '
               f'new WAV files: {totals["written"]:,}  empty audio: {totals["no_audio"]:,}  '
               f'transcripts with no usable audio: {totals["unmatched_transcripts"]:,}  '
               f'garbled prompts dropped: {totals["garbled_text"]:,}')
    report.insert(3, summary)
    (out / 'prepare_report.txt').write_text('\n'.join(report) + '\n', encoding='utf-8')
    print(f'\n{summary}\nWrote {out / "metadata.csv"} and prepare_report.txt')
    if totals['clips'] and totals['with_text'] < totals['clips'] * 0.5:
        print('\n! Fewer than half the clips got a transcript. Check prepare_report.txt: the file-name '
              'column in transcripts.csv may need --key-col, or the text column --text-col.')
    print(f'\nNext:\n  python ml\\data_check.py "{out}" --language {language} --out "{out / "_ndimi"}"')


if __name__ == '__main__':
    main()
