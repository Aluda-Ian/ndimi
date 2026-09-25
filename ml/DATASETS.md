# Ndimi datasets: connect, store, use

How Ndimi gets its speech datasets, where they live, and how they become models the dubbing
pipeline can call. Read this before downloading anything.

> **In one line:** get access on Hugging Face → download one language to a big local drive
> (never into Git) → run `data_check.py` → train in Colab → serve with `model_service` →
> switch it on in Ndimi Admin.

```mermaid
flowchart LR
    A["Hugging Face<br/>AfriVoices-KE"] -->|hf download<br/>one language| B["G:\Datasets\afrivoices-ke\&lt;lang&gt;"]
    B -->|data_check.py| C["_ndimi/<br/>inventory.md<br/>manifest.jsonl"]
    B -->|upload or<br/>download in Colab| D["Google Drive<br/>MyDrive/ndimi/dataset"]
    C --> D
    D -->|train_asr.ipynb| E["Trained model<br/>+ results.json"]
    E -->|models.json| F["model_service<br/>(GPU server)"]
    F -->|Admin → Integrations<br/>Ndimi models| G["Ndimi dubbing<br/>pipeline"]
```

---

## Contents

1. [What we have](#1-what-we-have)
2. [Get access](#2-get-access)
3. [Where datasets live](#3-where-datasets-live)
4. [Download](#4-download)
5. [Take stock with data_check](#5-take-stock-with-data_check)
6. [Use the data](#6-use-the-data)
7. [Reference: manifest.jsonl](#7-reference-manifestjsonl)
8. [Adding another dataset](#8-adding-another-dataset)
9. [Troubleshooting](#9-troubleshooting)
10. [Rules we don't break](#10-rules-we-dont-break)

---

## 1. What we have

**AfriVoices-KE** (published on Hugging Face as *African Next Voices – Kenya*): scripted and
unscripted speech across agriculture, health, finance and government topics, with transcripts,
translations, dialect labels and speaker IDs.

| Language | Ndimi code | Folder in the dataset | Published hours* | ElevenLabs covers it? |
|---|---|---|---:|---|
| Gĩkũyũ | `ki` | `kik/` | ~754 | Dubbing yes, TTS no |
| Dholuo | `luo` | `luo/` | ~723 | No, **our models are the only route** |
| Kalenjin | `kln` | `kln/` | ~521 | No, **our models are the only route** |
| Maa | `mas` | `mas/` | ~505 | No, **our models are the only route** |
| Somali | `so` | `som/` | ~502 | TTS yes, Dubbing v2 no |

\*From the dataset card. The total is about **850 GB**, roughly 100–200 GB per language.
Your own `inventory.md` (step 5) is the number to trust.

Where it's published:

| Hugging Face repo | Contents | Access |
|---|---|---|
| [`MCAA1-MSU/anv_data_ke`](https://huggingface.co/datasets/MCAA1-MSU/anv_data_ke) | All five languages, one folder each | Gated: name + institution, accept terms |
| [`Anv-ke/<Language>`](https://huggingface.co/Anv-ke) (`Dholuo`, `kikuyu`, `Kalenjin`, `Maasai`, `Somali`) | One repo per language | Accept conditions while signed in |

Use whichever one our access was granted on. Splits are speaker-disjoint `train / dev / dev_test`;
the official `test` set is held back by the consortium for public leaderboards.

**Licence:** CC BY 4.0 (credit the AfriVoices-KE authors wherever we describe models trained on it).
The terms forbid use for surveillance, discrimination, exploitation or profiling. Also check
the agreement from the research consortium: it may add conditions on top of the card.

---

## 2. Get access

Do this once per person who will download data.

1. **Hugging Face account.** Sign in at huggingface.co with a Jeota Media email.
2. **Request access.** Open the dataset repo (table above), fill in name and institution
   (*Jeota Media Limited*), accept the terms. Wait for the approval email if it's gated.
3. **Create a read token.** huggingface.co → Settings → Access Tokens → *New token* →
   type **Read**. Name it `ndimi-datasets-<your name>`.
4. **Store the token in the right places. Nowhere else.**

| Where | How | Why |
|---|---|---|
| Your computer | `pip install -U huggingface_hub` then `hf auth login` (paste the token) | Lets `hf download` work |
| Google Colab | 🔑 *Secrets* panel → add `HF_TOKEN` → toggle *Notebook access* | Colab reads it without it appearing in the notebook |
| Ndimi Admin | **Admin → Integrations → AfriVoices-KE dataset**: *Access token* = the token; *Extra config* `dataset_url` = the repo URL, `languages` = e.g. `["luo","kln","mas"]` | Team record of which repo and token we use. Stored encrypted. Nothing in Ndimi downloads data automatically; this is the reference copy |

> Never paste the token into a notebook cell, `.env` committed to Git, a chat, or a screenshot.
> If it leaks: delete it on Hugging Face and make a new one.

---

## 3. Where datasets live

Datasets are big and licensed, so they never travel with the code.

| Location | What goes there | What never goes there |
|---|---|---|
| `G:\Datasets\<dataset>\` (local, outside the repo) | **The full downloaded dataset** | — |
| `ml/data/` in the repo | Small samples for testing only (a few hundred clips) | Full datasets |
| `ml/out/` in the repo | Reports for small/sample runs | — |
| `MyDrive/ndimi/` on Google Drive | The slice being trained on, plus manifests and trained models | Anything we're not training on right now |
| GPU server `/models` | Trained models the service loads | Raw datasets |
| Git / GitHub / Vercel / Neon / R2 public buckets | — | **Audio, transcripts, manifests, models, tokens** |

`ml/data/`, `ml/out/`, `ml/models/` and `ml/model_service/models.json` are in `.gitignore`,
and the whole `ml/` folder is in `.vercelignore`, so the website deploy never includes them.

### Standard layout

```
G:\Datasets\
└── afrivoices-ke\
    ├── luo\                 ← as downloaded from Hugging Face, never edited by hand
    ├── kln\
    ├── mas\
    └── _ndimi\              ← written by data_check.py (--out), safe to delete and regenerate
        ├── inventory.md
        ├── inventory.json
        ├── manifest.jsonl
        └── audio_embedded\  ← only when audio is stored inside .parquet files
```

Why `_ndimi` inside the dataset folder: `manifest.jsonl` stores audio paths **relative to the
dataset folder**. Keeping the report there means the dataset folder and its manifest can be
copied to Drive (or anywhere) as one unit, and paths still resolve, including audio that
`data_check` extracted from parquet files.

**Disk space:** allow the download size plus ~10% for reports, and, for parquet datasets,
the same again for `audio_embedded\`. Download one language at a time.

---

## 4. Download

Pick a route. Route A keeps a full copy on our drive; Route B skips the upload to Drive.

### Route A: to the computer (Windows)

```powershell
pip install -U huggingface_hub
hf auth login

# One language only. Replace luo/* with kln/*, mas/*, kik/*, som/*
hf download MCAA1-MSU/anv_data_ke --repo-type dataset `
  --include "luo/*" --include "README.md" `
  --local-dir "G:\Datasets\afrivoices-ke"
```

For a per-language repo instead:

```powershell
hf download Anv-ke/Dholuo --repo-type dataset --local-dir "G:\Datasets\afrivoices-ke\luo"
```

- Interrupted? Run the same command again: it resumes and skips finished files.
- Want a quick first look? Download a few shards only, e.g. `--include "luo/train-0000*"`
  (look at the file list on the Hugging Face *Files* tab for the real names).

### Route B: straight into Google Drive from Colab

Useful when you only want to train one language and don't want to upload 100+ GB from the office
connection. In a Colab cell (with the `HF_TOKEN` secret enabled):

```python
from google.colab import drive, userdata
drive.mount("/content/drive")
from huggingface_hub import snapshot_download

snapshot_download(
    "MCAA1-MSU/anv_data_ke", repo_type="dataset",
    allow_patterns=["luo/*"],                       # one language
    local_dir="/content/drive/MyDrive/ndimi/dataset",
    token=userdata.get("HF_TOKEN"),
)
```

Then run step 5 in Colab too: `data_check.py` is plain Python.

> Google Drive space: a full language is 100–200 GB. For a first model, 20–50 hours is plenty.
> Download a subset of shards rather than the whole language.

---

## 5. Take stock with data_check

`ml/data_check.py` reads the dataset, measures every clip, flags problems and writes the
manifest the training notebook uses. Run it every time data is added or changed.

```powershell
cd "G:\Brands\Jeota Media\sauti-yetu-site\sauti-yetu"
pip install pyyaml pandas pyarrow soundfile      # parquet + mapping support

python ml\data_check.py "G:\Datasets\afrivoices-ke" `
  --mapping ml\mapping.afrivoices-ke.yaml `
  --out "G:\Datasets\afrivoices-ke\_ndimi"
```

| Option | Use when |
|---|---|
| `--mapping ml\mapping.afrivoices-ke.yaml` | Always for AfriVoices-KE: its speaker and dialect columns (`recorder_uuid`, `sentenceDialect`) have names `data_check` can't guess |
| `--out <dataset>\_ndimi` | Always (see [Standard layout](#standard-layout)) |
| `--language luo` | The folder holds one language and has no language column or language-named folders |
| `--fast` | First look at a huge download: uses durations from the metadata instead of opening every file |
| `--workers 16` | Fast disk and many CPU cores |

### Read `inventory.md`

It opens with one row per language:

| Column | Meaning |
|---|---|
| Usable for transcription (h) | Clean clips with transcript, 0.5–30 s, language known, no duplicates |
| Sentence pairs (EN / SW) | Transcript + translation pairs, for training translation later |
| Transcription | `not enough yet` (< 5 h) · `start (fine-tune and compare)` (5–20 h) · `ready` (≥ 20 h) |
| Translation | `start` once there are ~2,000 sentence pairs |
| Voice | `candidate` when one speaker has ≥ 1 h of clean audio |

Then, per language: speakers, gender balance, dialects, clip lengths, sample rates and a
**Problems found** list. The common ones:

| Problem | What to do |
|---|---|
| Language not identified | Add `--language` or a `language:` line in the mapping |
| No transcript | Fine for voice data; excluded from transcription training |
| Longer than 30 s | Excluded; unscripted recordings may need splitting before they're usable |
| Transcript far too long for the clip | Likely misaligned; excluded automatically |
| Sample rate below 16 kHz | Still usable, but expect lower accuracy |
| Listed in metadata but audio missing | Download incomplete: re-run `hf download` |

**Check the mapping worked:** in the console output, each metadata file prints the columns it
used (`text=transcript, speaker=recorder_uuid, …`). If `speaker` is missing, every clip lands
in a random split and test scores will look better than reality.

---

## 6. Use the data

### 6.1 Train speech-to-text (Colab)

1. Put the dataset folder on Drive as `MyDrive/ndimi/dataset/` (including `_ndimi/`),
   or use Route B.
2. Open `ml/train_asr.ipynb` in Colab → *Runtime → Change runtime type → GPU*.
3. Edit the settings cell:

   ```python
   LANGUAGE   = "luo"
   DRIVE      = "/content/drive/MyDrive/ndimi"
   MANIFEST   = f"{DRIVE}/dataset/_ndimi/manifest.jsonl"
   AUDIO_ROOT = f"{DRIVE}/dataset"
   OUTPUT_DIR = f"{DRIVE}/models/whisper-{LANGUAGE}"
   DIALECT    = None      # or e.g. "Nandi" for one dialect
   ```

4. *Runtime → Run all.* It prints WER before and after on held-out speakers and saves the
   model plus `results.json`. If Colab disconnects, run it again; it resumes.
5. Optional: paste an ElevenLabs key to score Scribe on the same clips.

**Decision rule:** switch a language to our own model only when its WER is clearly below Scribe's
on the same test speakers.

Suggested order: **Dholuo → Kalenjin → Maa** (no commercial alternative), then Gĩkũyũ and
Somali where we can compare against ElevenLabs.

### 6.2 Serve the model

1. Copy `ml/model_service/models.example.json` → `models.json` and add the model:
   ```json
   {"transcribe": {"luo": {"model": "/models/whisper-luo", "language_token": "swahili"}}}
   ```
2. Run the Docker image on a GPU host with `NDIMI_MODELS_TOKEN` set (see `ml/README.md`).
3. Open `https://<server>/health`: the language should be listed under `transcribe`.

### 6.3 Switch it on in Ndimi

1. **Admin → Integrations → Ndimi models (custom HTTP)**: base URL + the same bearer token → enable.
2. **Dubbing settings** → speech-to-text → *Ndimi models*.

> ⚠ That setting is global: every language the Ndimi pipeline transcribes goes to our service.
> Languages without a model return 422 and the job fails. Switch only when the service covers
> the languages you dub from, or add per-language routing first.

### 6.4 Later: translation and voices

- **Translation** (`inventory.md` shows `start`): fine-tune NLLB on the sentence pairs, add
  under `translate` in `models.json`.
- **Voice** (`candidate`): fine-tune a VITS/MMS voice on the top speaker's audio, add under `tts`.
  Check the consent terms cover synthesising that speaker's voice before you do.

---

## 7. Reference: manifest.jsonl

One JSON object per clip. The notebook and any future training script read only this file.

| Field | Example | Notes |
|---|---|---|
| `audio` | `luo/clips/a1b2.wav` | Relative to the dataset folder |
| `duration` | `6.42` | Seconds |
| `text` | `Ber ahinya` | Transcript in the spoken language |
| `language` | `luo` | Ndimi code: `sw ki luo kln mas so luy mer kam guz` |
| `dialect` | `South Nyanza` | Empty if not labelled |
| `speaker` | `7f3c…` | Used for the split |
| `gender` | `female` | |
| `translation_en` / `translation_sw` | | Empty if none |
| `sample_rate` | `16000` | |
| `split` | `train` · `dev` · `test` | Speaker-disjoint (kept from the dataset if it has a split column) |
| `usable_for_asr` | `true` | Filter on this for transcription training |
| `issues` | `["low_sample_rate"]` | Codes explained in `inventory.md` |

Load it anywhere:

```python
import json
rows = [json.loads(l) for l in open("manifest.jsonl", encoding="utf-8")]
luo_train = [r for r in rows if r["language"] == "luo" and r["split"] == "train" and r["usable_for_asr"]]
```

---

## 8. Adding another dataset

Common Voice, FLEURS, our own consented recordings, church media archives:

1. **Licence first:** commercial use? publishing or selling trained models? attribution? storage location?
2. Download to `G:\Datasets\<name>\` (same rules as above).
3. Run `data_check.py` with `--out G:\Datasets\<name>\_ndimi`. It understands CSV/TSV,
   JSON/JSONL, Hugging Face parquet, one `.txt` per clip and one folder per language.
4. If columns aren't picked up, copy `ml/mapping.example.yaml` to `ml/mapping.<name>.yaml`
   and fill in the names shown in `inventory.md`. Commit the mapping (it has no data in it).
5. Add a row to [What we have](#1-what-we-have) in this file.
6. To train on two datasets together, run `data_check.py` on a parent folder that contains both.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `403` / `GatedRepoError` on download | Access not approved yet, or logged in with another account | Check the repo page shows *You have been granted access*; `hf auth whoami` |
| `Skipping … install pandas + pyarrow` | Parquet support missing | `pip install pandas pyarrow` |
| `ModuleNotFoundError: yaml` | Mapping file needs PyYAML | `pip install pyyaml` |
| Everything in language `unknown` | Folders not named by language and no language column | `--language <code>` |
| Clips show duration `not measured` | Can't decode the format | Install ffmpeg or `pip install soundfile` |
| Notebook: *N audio files not found under AUDIO_ROOT* | `AUDIO_ROOT` isn't the folder `data_check` scanned | Point it at the dataset folder (the one that contains `_ndimi/`) |
| Notebook: *Too few speakers for a speaker-based split* | Speaker column not mapped | Check the mapping and re-run `data_check` |
| Colab runs out of GPU memory | Batch too big | `BATCH_SIZE = 8`, or `whisper-base` |
| Drive full | Whole language uploaded | Keep a subset of shards; delete `checkpoints/` after saving the final model |

---

## 10. Rules we don't break

- Datasets, manifests, models and tokens **never** go into Git, Vercel, Neon or a public bucket.
- The downloaded folder is **read-only** to us: fixes go in the mapping or in code, never by editing files.
- Every model we ship has a `results.json` showing it beat what it replaces on the same test speakers.
- We credit AfriVoices-KE and respect its prohibited uses and any consortium terms.
- Speakers' voices are only synthesised where their consent covers it.
