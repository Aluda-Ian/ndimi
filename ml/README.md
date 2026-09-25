# Ndimi models: from dataset to live model

This folder turns a speech dataset (for example AfriVoices-KE) into models Ndimi can use.
It is separate from the website: Vercel doesn't deploy it (see `.vercelignore`), and data or
model files never go into Git (see `.gitignore`).

**New here? Start with [DATASETS.md](DATASETS.md)**: getting access to AfriVoices-KE, where
datasets are stored, downloading, and turning them into models.

```
ml/
  data_check.py          1. take stock of the dataset, write a clean manifest
  DATASETS.md            how to get, store and use our datasets (start here)
  mapping.example.yaml      (only if data_check can't work out the columns itself)
  mapping.afrivoices-ke.yaml  column mapping for AfriVoices-KE
  train_asr.ipynb        2. fine-tune speech-to-text for one language in Google Colab
  model_service/         3. serve trained models to Ndimi
    app.py, ndimi_models.py, models.example.json, Dockerfile, requirements.txt, test_service.py
  out/                   reports and manifests (created by data_check, not in Git)
```

Before step 1, check the dataset licence: commercial use, whether trained models may be
published or sold, attribution, and where the data may be stored.

---

## 1. Take stock of the dataset

On your computer, from the project folder:

```
python ml/data_check.py "D:\datasets\afrivoices-ke"
```

It finds the audio, transcripts and translations on its own. It understands CSV/TSV,
JSON/JSONL, Hugging Face parquet (including audio stored inside the file), one-`.txt`-per-clip,
and one-folder-per-language layouts. It writes to `ml/out/<dataset name>/`:

| File | What it's for |
|---|---|
| `inventory.md` | **Read this.** Hours, speakers, dialects and problems per language, and whether each language is ready for transcription, translation or a voice |
| `inventory.json` | The same numbers, for scripts |
| `manifest.jsonl` | One line per clip in one standard format, with a train/dev/test split by speaker. The notebook reads this |

Useful options:
- `--language luo`: the whole folder is one language
- `--mapping ml/mapping.yaml`: name the columns yourself (copy `mapping.example.yaml`)
- `--fast`: don't open the audio, use durations from the metadata (quick first look at a huge dataset)

It runs on plain Python. For formats other than WAV, install ffmpeg or `pip install soundfile`;
for `.parquet`, `pip install pandas pyarrow`.

## 2. Train speech-to-text for one language

Start with the language that has the most **usable transcription hours** in `inventory.md`.

1. Upload the dataset folder and `manifest.jsonl` to Google Drive, e.g. `MyDrive/ndimi/dataset/`
   and `MyDrive/ndimi/out/manifest.jsonl`.
2. Open `ml/train_asr.ipynb` in Google Colab (File → Upload notebook), switch the runtime to GPU.
3. Set `LANGUAGE` and the paths in the first cell, then **Runtime → Run all**.

The notebook scores the model on held-out speakers before and after training (word error rate,
lower is better), can score ElevenLabs Scribe on the same clips, and saves the model plus
`results.json` to Drive. If Colab disconnects, run it again: it resumes from the last checkpoint.

**Rule of thumb:** only switch a language to your own model when its WER is clearly below Scribe's.

## 3. Serve the model to Ndimi

Ndimi talks to your models through one small web service (`model_service/`) that follows the
contract in `dubbing/providers/custom.py`.

1. Copy `models.example.json` to `models.json` and list your trained models, e.g.
   ```json
   {"transcribe": {"luo": {"model": "/models/whisper-luo", "language_token": "swahili"}}}
   ```
   `model` can be a folder or a (private) Hugging Face repo. Translation and voice entries work the same way.
2. Run it somewhere with a GPU (it also works on CPU, just slower). With Docker:
   ```
   cd ml/model_service
   docker build -t ndimi-models .
   docker run --gpus all -p 8000:8000 -e NDIMI_MODELS_TOKEN=<long random string> \
     -v $PWD/models.json:/app/models.json -v /path/to/models:/models ndimi-models
   ```
   For a private Hugging Face repo, also pass `-e HF_TOKEN=<read token>`.
   Any host that runs a Docker container with a GPU works (a cloud GPU server, Hugging Face Spaces, etc.).
3. Check it: open `https://<your-server>/health`. It lists the configured languages.
4. In Ndimi: **Admin → Integrations → Ndimi models (custom HTTP)**: set the base URL and the same
   bearer token, and switch it on. Then in **Dubbing settings** choose "Ndimi models" for
   speech-to-text.

   Note: that setting currently applies to **every** language the pipeline dubs from. The service
   answers clearly when it has no model for a language, but those jobs will fail, so switch over
   when your models cover the languages you use, or ask for per-language routing in Ndimi.

Test the service logic without downloading any models: `cd ml/model_service && python test_service.py`.

## What comes next

- **Translation:** once `inventory.md` shows enough sentence pairs, fine-tune a translation
  model (e.g. NLLB) the same way and add it under `translate` in `models.json`.
- **Voices:** a language with an hour or more from one clear speaker can get its own voice;
  add it under `tts`. Until then keep ElevenLabs for voices, and turn off voice cloning when
  using Ndimi voices.
- **Keep improving:** retrain as reviewed dubs and new consented recordings add data, and
  compare every new model against the current one on the same test speakers.
