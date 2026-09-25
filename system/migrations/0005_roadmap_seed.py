"""Loads the Ndimi project roadmap: every milestone and task from start to finish.

Tasks already finished when the roadmap was added are marked done. Edit, add or remove
tasks afterwards in Settings → Project roadmap; this migration only runs once.
"""
from django.db import migrations
from django.utils import timezone

# Each task: (title, area, details, reference, command, done)
ROADMAP = [
    {
        'code': 'M0', 'phase': 'foundation', 'title': 'Foundations & governance',
        'goal': 'Accounts, agreements and rules are in place before we store or use anyone\'s voice.',
        'tasks': [
            ('Register the domain and project mailbox', 'operations',
             'ndimi.jeotamedia.co.ke with DNS in DirectAdmin; mailbox info@ndimi.jeotamedia.co.ke '
             '(mail.ndimi.jeotamedia.co.ke, SMTP 587).', 'DirectAdmin', '', True),
            ('Set up the code repository, Vercel and Neon', 'platform',
             'Code on GitHub, deployed by Vercel on every push, production database on Neon Postgres.',
             'GitHub · Vercel · Neon', '', True),
            ('Name an owner for each workstream', 'business',
             'Platform, data & models, community & pilots, business & funding. Write the names into the '
             '"Owner" field of the tasks in this roadmap.', 'Settings → Project roadmap', '', False),
            ('Put all team accounts in a shared password manager with two-factor sign-in', 'operations',
             'Hugging Face, ElevenLabs, OpenAI, Google (Colab/Drive), GitHub, Vercel, Neon, DirectAdmin, '
             'the GPU host. Turn on two-factor authentication everywhere. Never share passwords in chat or email.',
             '', '', False),
            ('Change the Hugging Face password and turn on two-factor authentication', 'operations',
             'The password was shared in a chat, so treat it as exposed. Change it on huggingface.co → Settings → '
             'Password, then enable 2FA. Use read tokens (not the password) for downloads.',
             'huggingface.co → Settings', '', False),
            ('Accept the AfriVoices-KE dataset terms on Hugging Face', 'legal',
             'Done for the Anv-ke repos. Licence CC BY 4.0; no surveillance, discrimination, exploitation or profiling.',
             'ml/DATASETS.md §1', '', True),
            ('Get the research consortium\'s terms in writing', 'legal',
             'Confirm in writing: commercial use by Ndimi, publishing or selling models trained on the data, '
             'where the data may be stored, and how to credit them. File the reply with this task\'s notes.',
             '', '', False),
            ('Register with the Office of the Data Protection Commissioner (ODPC)', 'legal',
             'Kenya Data Protection Act 2019: register Jeota Media as a data controller/processor, because we '
             'process users\' uploads and voice recordings. Get advice from a lawyer on the category and fee.',
             'odpc.go.ke', '', False),
            ('Write the consent and recording policy', 'legal',
             'How we ask for consent to record, what voices may be used for (training, synthetic voices, '
             'publishing), how long we keep recordings, and how someone withdraws. Needed before any community '
             'recording or voice cloning.', '', '', False),
            ('Publish a privacy policy and terms of service', 'legal',
             'Cover uploads, voice cloning, third-party processors (ElevenLabs, OpenAI, Google), data location '
             'and deletion. Link both from sign-up and the landing page.', 'landing.html · sign-up page', '', False),
        ],
    },
    {
        'code': 'M1', 'phase': 'phase1', 'title': 'Platform in production',
        'goal': 'Ndimi runs at ndimi.jeotamedia.co.ke with sign-in, the Settings dashboard, email, storage and a '
                'running dub worker.',
        'tasks': [
            ('Build accounts: sign-in, sign-up, email verification, password reset', 'platform', '', 'accounts app', '', True),
            ('Build the Settings dashboard, permissions, integrations and activity log', 'platform', '',
             'system app · /#settings', '', True),
            ('Build the dubbing engine and job queue (ElevenLabs + Ndimi pipeline)', 'platform', '',
             'dubbing app', '', True),
            ('Set the production environment variables on Vercel', 'platform',
             'DJANGO_SECRET_KEY (long random), FIELD_ENCRYPTION_KEY (keep a safe copy: without it saved API keys '
             'can\'t be read), DATABASE_URL (Neon), DJANGO_ALLOWED_HOSTS=ndimi.jeotamedia.co.ke, DEBUG off.',
             'Vercel → Project → Settings → Environment Variables',
             'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"', False),
            ('Run the database migrations on Neon', 'platform',
             'With DATABASE_URL pointing at Neon. This also creates this roadmap in production.',
             '', 'python manage.py migrate', False),
            ('Point ndimi.jeotamedia.co.ke at Vercel and check HTTPS', 'operations',
             'Add the domain in Vercel, set the DNS records it shows in DirectAdmin, wait for the certificate.',
             'Vercel → Domains · DirectAdmin → DNS', '', False),
            ('Create the team\'s admin accounts and access groups', 'platform',
             'One account per person (no shared logins). Give each person only the groups they need.',
             'Settings → Users · Access rights', 'python manage.py createsuperuser', False),
            ('Set up email and send a test', 'platform',
             'SMTP host mail.ndimi.jeotamedia.co.ke, port 587, STARTTLS, user info@ndimi.jeotamedia.co.ke. '
             'Use "Save and send test email". Then turn on email verification if wanted.',
             'Settings → Email (SMTP)', '', False),
            ('Connect permanent media storage (Cloudflare R2 or S3)', 'platform',
             'Vercel\'s disk is temporary, so uploads and finished dubs must live in object storage. '
             'Add the keys under Integrations and make sure uploads and outputs are written there.',
             'Settings → Integrations → Cloudflare R2', '', False),
            ('Run the dub worker on an always-on machine', 'operations',
             'Vercel can\'t run background jobs. Run the worker on a small always-on server (or the GPU host) '
             'with the same DATABASE_URL and storage settings. The Overview page warns when dubs wait with no worker.',
             'Settings → Overview', 'python manage.py run_dub_worker', False),
            ('Turn on database backups and test a restore', 'operations',
             'Neon keeps point-in-time history; note the retention period, and do one test restore to a branch.',
             'Neon console', '', False),
            ('Set up error alerts and uptime monitoring', 'operations',
             'Get an email when the site is down or errors spike (e.g. Vercel logs + an uptime checker).', '', '', False),
        ],
    },
    {
        'code': 'M2', 'phase': 'phase1', 'title': 'Dubbing live with ElevenLabs (Kiswahili & Gĩkũyũ)',
        'goal': 'Anyone with access can upload a video and download a reviewed Kiswahili or Gĩkũyũ dub.',
        'tasks': [
            ('Add the ElevenLabs and OpenAI keys and switch them on', 'platform',
             'Start in sandbox with low spending limits on each provider\'s own dashboard.',
             'Settings → Integrations', '', False),
            ('Check the dubbing engine settings', 'platform',
             'ElevenLabs dubbing languages: sw, ki. Review gate on. Confirm the default voices and time-fit limits.',
             'Settings → Dubbing engine', '', False),
            ('Run five short test dubs in each language', 'operations',
             'Mix of talking head, several speakers, background music. Note any failures in this task\'s notes.',
             'New dub', '', False),
            ('Native-speaker quality review of the test dubs', 'community',
             'Score each dub 1–5 for meaning, naturalness, timing and voice match. Keep the scoring sheet so later '
             'versions are compared the same way.', '', '', False),
            ('Agree the review workflow', 'operations',
             'Who approves scripts before voices are made, how quickly, and what happens when a dub is rejected.',
             'Settings → Dub jobs', '', False),
            ('Work out the cost per finished minute', 'business',
             'From provider invoices after the test dubs. Needed for pricing (M10).', '', '', False),
        ],
    },
    {
        'code': 'M3', 'phase': 'phase1', 'title': 'AfriVoices-KE data ready to train on',
        'goal': 'Every language we need is downloaded, joined, checked and summarised in inventory.md.',
        'tasks': [
            ('Get access to the AfriVoices-KE (Anv-ke) datasets', 'data', 'Access confirmed for Anv-ke/kikuyu.',
             'huggingface.co/Anv-ke', '', True),
            ('Write the dataset guide', 'data', 'How to get access, where data lives, download, prepare and use.',
             'ml/DATASETS.md', '', True),
            ('Build the preparation and checking tools', 'data',
             'prepare_afrivoices.py joins parquet audio with transcripts.csv; data_check.py measures, flags '
             'problems and writes the manifest.', 'ml/', '', True),
            ('Check the real file layout and columns (Gĩkũyũ)', 'data',
             'transcripts.csv: mediaPathId, actualSentence (text read aloud), translatedText (Kiswahili/English '
             'source); meta.csv: one row per speaker. dev/scripted = 6,824 clips, 8.4 h, 24 speakers.',
             'ml/DATASETS.md §1', '', True),
            ('Create a Hugging Face read token and store it', 'data',
             'Log in on each machine with the token, and keep a copy in the AfriVoices-KE integration '
             '(Extra config: dataset_url, languages).', 'Settings → Integrations → AfriVoices-KE dataset',
             'hf auth login', False),
            ('Get a drive with at least 1 TB free for datasets', 'operations',
             'Raw download + prepared WAVs take about 2× the download (≈450 GB for Gĩkũyũ alone). '
             'Keep datasets outside the code folder, e.g. G:\\Datasets.', 'ml/DATASETS.md §3', '', False),
            ('Trial run: Gĩkũyũ dev/scripted (2.6 GB)', 'data',
             'Download, prepare and check the smallest slice to prove the whole chain works.',
             'ml/DATASETS.md §4–5',
             'hf download Anv-ke/kikuyu --repo-type dataset --revision b0e2cd2b0aef5de69813455ef62134e23678c3e0 '
             '--include "dev/scripted/*" --local-dir "G:\\Datasets\\afrivoices-ke\\kikuyu"\n'
             'python ml\\prepare_afrivoices.py "G:\\Datasets\\afrivoices-ke\\kikuyu"\n'
             'python ml\\data_check.py "G:\\Datasets\\afrivoices-ke\\kikuyu-prepared" --language ki '
             '--out "G:\\Datasets\\afrivoices-ke\\kikuyu-prepared\\_ndimi"', False),
            ('Review prepare_report.txt and inventory.md from the trial', 'data',
             'Check most clips matched a transcript, speakers and dialects look right, and the problem list is small.',
             'kikuyu-prepared\\prepare_report.txt · _ndimi\\inventory.md', '', False),
            ('Check the unscripted transcripts before downloading 145 GB', 'data',
             'Download dev/unscripted/files/*.csv only and confirm which column holds the transcript.',
             '', 'hf download Anv-ke/kikuyu --repo-type dataset --include "dev/unscripted/files/*" --local-dir ml\\data\\kikuyu', False),
            ('Download and prepare the rest of Gĩkũyũ', 'data',
             'Scripted train + dev_test first (≈49 GB), unscripted later. Use the pinned revision.',
             'ml/DATASETS.md §4', '', False),
            ('Download, prepare and check Dholuo', 'data', 'Anv-ke/Dholuo → G:\\Datasets\\afrivoices-ke\\dholuo',
             'ml/DATASETS.md §4', '', False),
            ('Download, prepare and check Kalenjin', 'data', 'Anv-ke/Kalenjin. Check the dialect balance in inventory.md.',
             'ml/DATASETS.md §4', '', False),
            ('Download, prepare and check Maa', 'data', 'Anv-ke/Maasai → …\\maasai', 'ml/DATASETS.md §4', '', False),
            ('Download, prepare and check Somali', 'data', 'Anv-ke/Somali → …\\somali', 'ml/DATASETS.md §4', '', False),
            ('Record hours, speakers and dialects per language', 'data',
             'Copy the summary row of each inventory.md into this task\'s notes, with the dataset revision.',
             '', '', False),
        ],
    },
    {
        'code': 'M4', 'phase': 'phase1', 'title': 'First own model: Gĩkũyũ speech-to-text',
        'goal': 'A fine-tuned Gĩkũyũ model, scored on held-out speakers against ElevenLabs Scribe, with a '
                'recorded decision.',
        'tasks': [
            ('Set up Google Colab with a GPU and enough Drive space', 'models',
             'Colab Pro gives steadier GPUs. Drive needs room for the prepared data you upload plus checkpoints.',
             'colab.research.google.com', '', False),
            ('Upload the prepared Gĩkũyũ folder to Drive', 'data',
             'kikuyu-prepared (scripted train + dev + test, including _ndimi) → MyDrive/ndimi/dataset.',
             'ml/DATASETS.md §6.1', '', False),
            ('Quick end-to-end run (MAX_STEPS = 500)', 'models',
             'Proves the notebook, paths and GPU work before a long run.', 'ml/train_asr.ipynb', '', False),
            ('Full training run (whisper-small, 4,000 steps)', 'models',
             'Save results.json. Write the WER before and after into this task\'s notes.', 'ml/train_asr.ipynb', '', False),
            ('Score ElevenLabs Scribe on the same test clips', 'models',
             'Paste an ElevenLabs key in the notebook settings. Costs roughly the test-set length in credits.',
             'ml/train_asr.ipynb §8', '', False),
            ('Native speaker checks 50 model transcripts', 'community',
             'Note the kinds of errors (names, numbers, dialect words, tone marks ĩ/ũ).', '', '', False),
            ('Decide: ship, retrain or add data', 'models',
             'Switch only if our WER is clearly below Scribe\'s. Otherwise try unscripted data, more steps or '
             'whisper-medium. Record the decision and why.', '', '', False),
            ('Save the model to a private Hugging Face repo', 'models',
             'e.g. jeotamedia/whisper-small-kikuyu, with a model card crediting AfriVoices-KE.',
             'train_asr.ipynb → HF_REPO', '', False),
        ],
    },
    {
        'code': 'M5', 'phase': 'phase1', 'title': 'Model service connected to Ndimi',
        'goal': 'Ndimi sends audio to our own model server for the languages we choose and gets transcripts back.',
        'tasks': [
            ('Choose a GPU host and set a monthly budget', 'operations',
             'Options: RunPod, Lambda, a cloud VM, Hugging Face Inference Endpoints. Compare cost per hour and '
             'whether it can stay on or scale to zero.', '', '', False),
            ('Deploy the model service with Docker', 'platform',
             'Copy models.example.json to models.json, list the trained models, set a long random '
             'NDIMI_MODELS_TOKEN (and HF_TOKEN for private repos).', 'ml/model_service · ml/README.md §3',
             'docker build -t ndimi-models .\n'
             'docker run --gpus all -p 8000:8000 -e NDIMI_MODELS_TOKEN=<token> -v $PWD/models.json:/app/models.json ndimi-models',
             False),
            ('Check /health lists the languages', 'platform', 'Open https://<server>/health in a browser.', '', '', False),
            ('Connect Ndimi to the service', 'platform', 'Base URL + the same bearer token, then switch it on.',
             'Settings → Integrations → Ndimi models', '', False),
            ('Add per-language speech-to-text routing', 'platform',
             'Today the speech-to-text choice applies to every language. Add a per-language setting so our models '
             'handle ki/luo/kln/mas while other languages stay on ElevenLabs.', 'dubbing app', '', False),
            ('End-to-end test: dub a Gĩkũyũ video using our model', 'operations',
             'Compare with the same video through ElevenLabs; note differences in the notes.', 'New dub', '', False),
        ],
    },
    {
        'code': 'M6', 'phase': 'phase1', 'title': 'Speech-to-text for Dholuo, Kalenjin, Maa and Somali',
        'goal': 'A tested speech-to-text model for every AfriVoices-KE language, live in the model service.',
        'tasks': [
            ('Train and evaluate Dholuo speech-to-text', 'models',
             'No commercial alternative exists, so compare against the untrained baseline and native-speaker review.',
             'ml/train_asr.ipynb', '', False),
            ('Train and evaluate Kalenjin speech-to-text', 'models',
             'Check results per dialect; train per dialect if one dominates.', 'ml/train_asr.ipynb', '', False),
            ('Train and evaluate Maa speech-to-text', 'models', '', 'ml/train_asr.ipynb', '', False),
            ('Train and evaluate Somali speech-to-text', 'models', 'Compare with ElevenLabs Scribe, which supports Somali.',
             'ml/train_asr.ipynb', '', False),
            ('Native-speaker review for each new language', 'community', 'Same 50-transcript check as Gĩkũyũ.', '', '', False),
            ('Add the approved models to the model service', 'platform', 'Update models.json and restart; check /health.',
             'ml/model_service/models.json', '', False),
        ],
    },
    {
        'code': 'M7', 'phase': 'phase1', 'title': 'Translation into local languages',
        'goal': 'English/Kiswahili → local-language translation good enough for dubbing scripts after human review.',
        'tasks': [
            ('Extract sentence pairs per language', 'data',
             'From manifest.jsonl: text with translation_sw / translation_en. Gĩkũyũ dev/scripted alone has ~5,600 '
             'Kiswahili pairs.', '_ndimi/manifest.jsonl', '', False),
            ('Score the baseline translation model', 'models',
             'NLLB-200 where it covers the language (e.g. kik_Latn, luo_Latn, som_Latn); measure chrF on held-out pairs.',
             '', '', False),
            ('Fine-tune translation per language', 'models', 'Start with Gĩkũyũ and Dholuo.', '', '', False),
            ('Translator review and a shared glossary', 'community',
             'Names, place names, church and health terms. Load the glossary into dubbing settings.', '', '', False),
            ('Add translation models to the service and switch them on', 'platform',
             'Add under "translate" in models.json; choose "Ndimi models" for translation.',
             'Settings → Dubbing engine', '', False),
        ],
    },
    {
        'code': 'M8', 'phase': 'phase1', 'title': 'Our own voices (text-to-speech)',
        'goal': 'A natural, consented voice for each language ElevenLabs can\'t speak.',
        'tasks': [
            ('Confirm consent covers synthetic voices', 'legal',
             'Dataset consent may not cover cloning a speaker\'s voice. If not, record consented voice talent.',
             'Consent policy (M0)', '', False),
            ('Record or choose one clear speaker per language (1–2 hours)', 'data',
             'Quiet room, one microphone, varied sentences. inventory.md shows "voice: candidate" when a dataset '
             'speaker has enough.', '', '', False),
            ('Train a voice per language (MMS / VITS)', 'models', '', '', '', False),
            ('Listening test with native speakers', 'community', 'Score naturalness and clarity 1–5 against ElevenLabs where possible.',
             '', '', False),
            ('Add voices to the model service', 'platform',
             'Add under "tts" in models.json; turn voice cloning off for those languages.',
             'Settings → Dubbing engine', '', False),
        ],
    },
    {
        'code': 'M9', 'phase': 'phase1', 'title': 'Pilot in Kajiado and Meru',
        'goal': 'Communities use Ndimi on Jeota Media\'s documentaries and we know what to fix before launch.',
        'tasks': [
            ('Choose the pilot films and languages', 'community',
             'Maa for Kajiado. Kimeru for Meru has no dataset yet, so plan recording (see M12).', '', '', False),
            ('Sign pilot partners', 'business', 'An NGO, a church media team and a county office, with a simple agreement.',
             '', '', False),
            ('Dub the pilot content', 'operations', '', 'New dub', '', False),
            ('Community screenings and feedback sessions', 'community',
             'Record what people understood, what sounded wrong, and what they\'d use it for.', '', '', False),
            ('Collect consented recordings during the pilot', 'data',
             'Only under the consent policy. Log each session.', 'Consent policy (M0)', '', False),
            ('Write the pilot report for grant partners', 'business',
             'Minutes dubbed, turnaround, cost per minute, quality scores, quotes, what changes next.', '', '', False),
        ],
    },
    {
        'code': 'M10', 'phase': 'phase1', 'title': 'Public launch',
        'goal': 'Ndimi is open to the public and paying users, with support in place.',
        'tasks': [
            ('Set pricing and plans', 'business', 'Based on cost per minute (M2) and pilot feedback.', '', '', False),
            ('Test and switch on payments (M-Pesa Daraja, Stripe)', 'platform',
             'Sandbox first, then live keys.', 'Settings → Integrations', '', False),
            ('Write help pages and onboarding', 'business', 'How to upload, review a script, download.', '', '', False),
            ('Security review before launch', 'platform',
             'Permissions, rate limits, secrets rotated, backups restored once, admin accounts reviewed.',
             'Settings → Users · Activity log', '', False),
            ('Set up support', 'operations', 'Support email in System settings, response times, who answers.',
             'Settings → System', '', False),
            ('Publish model cards and credit AfriVoices-KE', 'legal',
             'For every public model: data used, languages, known limits, licence and attribution.', '', '', False),
            ('Launch announcement', 'business', 'Landing page, partners, press.', 'landing.html', '', False),
        ],
    },
    {
        'code': 'M11', 'phase': 'phase2', 'title': 'Live translation',
        'goal': 'Real-time spoken translation for meetings, services and trainings.',
        'tasks': [
            ('Measure end-to-end delay (speech → text → translation → voice)', 'models',
             'Target under 3 seconds for one language pair.', '', '', False),
            ('Build a prototype for Kiswahili ↔ Gĩkũyũ', 'platform', '', '', '', False),
            ('Field test at a community meeting', 'community', '', '', '', False),
            ('Decide hardware and offline mode for venues with poor internet', 'operations', '', '', '', False),
        ],
    },
    {
        'code': 'M12', 'phase': 'phase3', 'title': 'Learn',
        'goal': 'Diaspora and young learners can learn a Kenyan language from real dubbed content.',
        'tasks': [
            ('Design the learning experience with learners', 'community', 'Interviews with diaspora families and youth.',
             '', '', False),
            ('Turn dubbed content into lessons', 'platform', 'Bilingual subtitles, vocabulary, replay by sentence.',
             '', '', False),
            ('Pronunciation feedback using our speech-to-text', 'models', '', '', '', False),
            ('Pilot with a diaspora group', 'community', '', '', '', False),
        ],
    },
    {
        'code': 'M13', 'phase': 'ongoing', 'title': 'Keep improving',
        'goal': 'Models get better every quarter and new languages are added as data is collected.',
        'tasks': [
            ('Retrain with reviewed dubs and new consented recordings', 'models',
             'Compare every new model with the current one on the same test speakers before switching.', '', '', False),
            ('Collect data for Kimeru, Oluluhya, Kikamba and Ekegusii', 'data',
             'Community recording drives under the consent policy.', '', '', False),
            ('Quarterly report to funders and partners', 'business', '', '', '', False),
            ('Quarterly security and access review', 'operations', 'Remove old accounts, rotate API keys, check backups.',
             'Settings → Users · Integrations', '', False),
        ],
    },
]


def load(apps, schema_editor):
    Milestone = apps.get_model('system', 'Milestone')
    RoadmapTask = apps.get_model('system', 'RoadmapTask')
    if Milestone.objects.exists():
        return
    now = timezone.now()
    for m_order, m in enumerate(ROADMAP, start=1):
        milestone = Milestone.objects.create(code=m['code'], title=m['title'], phase=m['phase'],
                                             goal=m['goal'], order=m_order * 10)
        for t_order, (title, area, details, reference, command, done) in enumerate(m['tasks'], start=1):
            RoadmapTask.objects.create(
                milestone=milestone, order=t_order * 10, title=title, area=area, details=details,
                reference=reference, command=command, done=done, done_at=now if done else None,
                notes='Already done when the roadmap was added.' if done else '',
            )
        if all(t[5] for t in m['tasks']):
            milestone.completed_at = now
            milestone.save(update_fields=['completed_at'])


def unload(apps, schema_editor):
    apps.get_model('system', 'Milestone').objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [('system', '0004_roadmap')]
    operations = [migrations.RunPython(load, unload)]
