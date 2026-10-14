from django.db import migrations, models

# Licences and language coverage checked October 2026 (model cards on Hugging Face). Recheck before release.
MODELS = [
    # key, name, task, runner, model_ref, languages, source_languages, licence, commercial, enabled, priority, docs, notes
    ('ndimi-whisper', 'Ndimi Whisper (our fine-tunes)', 'stt', 'ndimi', 'whisper-small fine-tuned on AfriVoices-KE',
     '', '*', 'MIT (Whisper) + CC BY 4.0 data', True, False, 5, 'https://huggingface.co/openai/whisper-small',
     'Add each language code here once its model beats Scribe on the same test speakers (results.json).'),
    ('elevenlabs-scribe', 'ElevenLabs Scribe', 'stt', 'elevenlabs', 'scribe_v2', '*', '*',
     'Commercial API (ElevenLabs terms)', True, True, 10, 'https://elevenlabs.io/docs', 'Speakers and word timings.'),
    ('openai-whisper', 'OpenAI Whisper API', 'stt', 'openai', 'whisper-1', '*', '*',
     'Commercial API (OpenAI terms)', True, True, 20, 'https://platform.openai.com/docs', 'Fallback; no speaker labels.'),

    ('ndimi-mt', 'Ndimi translation (our fine-tunes)', 'translate', 'ndimi', 'fine-tuned on AfriVoices-KE sentence pairs',
     '', 'en,sw', 'Base model licence + CC BY 4.0 data', True, False, 5, '',
     'Add each language once native reviewers accept ≥80% of lines.'),
    ('madlad-400', 'MADLAD-400 (Google)', 'translate', 'ndimi', 'google/madlad400-3b-mt', 'mas,sw,so,en', '*',
     'Apache-2.0', True, False, 10, 'https://huggingface.co/google/madlad400-3b-mt',
     'Commercial use allowed. Has Maa, Kiswahili and Somali; not Dholuo, Gĩkũyũ or Kalenjin. Runs on the Ndimi model service.'),
    ('nllb-200', 'NLLB-200 (Meta)', 'translate', 'ndimi', 'facebook/nllb-200-distilled-600M', 'luo,ki,kam,sw,so,en', '*',
     'CC-BY-NC-4.0 (non-commercial)', False, False, 15, 'https://huggingface.co/facebook/nllb-200-distilled-600M',
     'Non-commercial only: comparison and internal tests. Has Dholuo and Gĩkũyũ; no Kalenjin or Maa.'),
    ('openai-gpt', 'OpenAI (GPT)', 'translate', 'openai', '', '*', '*', 'Commercial API (OpenAI terms)', True, True, 30,
     'https://platform.openai.com/docs', 'Context- and timing-aware. Weak for low-resource languages.'),
    ('google-translate', 'Google Translate', 'translate', 'google', '', '*', '*', 'Commercial API (Google terms)', True, True, 40,
     'https://cloud.google.com/translate/docs', 'Fallback.'),

    ('ndimi-voices', 'Ndimi voices (trained on AfriVoices-KE)', 'tts', 'ndimi', 'VITS-style voices trained on AfriVoices-KE',
     '', '*', 'Ours (CC BY 4.0 data; speaker consent required)', True, False, 5, '',
     'Add a language once native listeners rate it ≥3.8/5. Check speaker consent for voice synthesis first.'),
    ('mms-tts', 'MMS voices (Meta)', 'tts', 'ndimi', 'facebook/mms-tts-<language>', '', '*', 'CC-BY-NC-4.0 (non-commercial)',
     False, False, 20, 'https://huggingface.co/docs/transformers/model_doc/mms',
     'Non-commercial only: comparison and internal tests. Fill in languages after checking which mms-tts checkpoints exist.'),
    ('elevenlabs-v3', 'ElevenLabs v3 (voice cloning)', 'tts', 'elevenlabs', 'eleven_v3', '*', '*',
     'Commercial API (ElevenLabs terms)', True, True, 30, 'https://elevenlabs.io/docs',
     'Speaks Kiswahili and Somali well. Other Kenyan languages only approximately: replaced as soon as our voices are ready.'),
]


def seed(apps, schema_editor):
    AIModel = apps.get_model('dubbing', 'AIModel')
    for (key, name, task, runner, ref, langs, sources, licence, commercial, enabled, priority, docs, notes) in MODELS:
        AIModel.objects.get_or_create(key=key, defaults=dict(
            name=name, task=task, runner=runner, model_ref=ref, languages=langs, source_languages=sources,
            licence=licence, commercial_use=commercial, enabled=enabled, priority=priority, docs_url=docs, notes=notes))
    # Switch the three steps to Automatic. The seeded API models reproduce today's behaviour until our own are enabled.
    DubbingSettings = apps.get_model('dubbing', 'DubbingSettings')
    DubbingSettings.objects.filter(pk=1).update(stt_provider='auto', translation_provider='auto', tts_provider='auto')


class Migration(migrations.Migration):

    dependencies = [
        ('dubbing', '0006_dubjob_archived'),
    ]

    operations = [
        migrations.CreateModel(
            name='AIModel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.SlugField(help_text='Code name sent to the model service, e.g. madlad-400.', max_length=60, unique=True)),
                ('name', models.CharField(max_length=100)),
                ('task', models.CharField(choices=[('stt', 'Speech-to-text'), ('translate', 'Translation'), ('tts', 'Voice (text-to-speech)')], max_length=10)),
                ('runner', models.CharField(choices=[('elevenlabs', 'ElevenLabs API'), ('openai', 'OpenAI API'), ('google', 'Google Cloud API'), ('ndimi', 'Ndimi model service (our GPU server)')], max_length=12, verbose_name='Runs on')),
                ('model_ref', models.CharField(blank=True, help_text='What runs: API model ID or Hugging Face repo, e.g. google/madlad400-3b-mt.', max_length=200, verbose_name='Model')),
                ('languages', models.CharField(blank=True, help_text='Ndimi codes it produces or understands, comma-separated (e.g. luo,ki). * = any language.', max_length=300)),
                ('source_languages', models.CharField(blank=True, default='*', help_text='Translation only: languages it translates from. * = any.', max_length=300)),
                ('licence', models.CharField(blank=True, max_length=120)),
                ('commercial_use', models.BooleanField(default=True, help_text='Off: never used for customer dubs unless "Allow non-commercial models" is on.', verbose_name='Commercial use allowed')),
                ('enabled', models.BooleanField(default=False)),
                ('priority', models.PositiveSmallIntegerField(default=50, help_text='Lower wins. Our trained models: 5; open models: 10–20; APIs: 30+.')),
                ('results', models.JSONField(blank=True, default=dict, help_text='Test scores per language, e.g. {"luo": {"chrF": 41.2, "accepted": 0.83}}.')),
                ('notes', models.TextField(blank=True)),
                ('docs_url', models.URLField(blank=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': 'AI model', 'ordering': ['task', 'priority', 'name']},
        ),
        migrations.AddField(
            model_name='dubbingsettings',
            name='allow_noncommercial_models',
            field=models.BooleanField(default=False, help_text='Off for real customers. On only for internal tests: lets Automatic pick models whose licence forbids commercial use (e.g. NLLB-200, MMS voices).', verbose_name='Allow non-commercial models'),
        ),
        migrations.AlterField(
            model_name='dubbingsettings', name='stt_provider',
            field=models.CharField(choices=[('auto', 'Automatic: best allowed model for each language (see AI models)'), ('elevenlabs', 'ElevenLabs Scribe (speakers + word timings)'), ('openai', 'OpenAI Whisper'), ('custom', 'Ndimi models (custom HTTP)')], default='auto', max_length=12, verbose_name='Speech-to-text'),
        ),
        migrations.AlterField(
            model_name='dubbingsettings', name='translation_provider',
            field=models.CharField(choices=[('auto', 'Automatic: best allowed model for each language (see AI models)'), ('openai', 'OpenAI (context-aware, timing-aware)'), ('google', 'Google Translate'), ('custom', 'Ndimi models (custom HTTP)'), ('none', 'None: humans translate in review')], default='auto', max_length=12, verbose_name='Translation'),
        ),
        migrations.AlterField(
            model_name='dubbingsettings', name='tts_provider',
            field=models.CharField(choices=[('auto', 'Automatic: best allowed model for each language (see AI models)'), ('elevenlabs', 'ElevenLabs (voice cloning)'), ('custom', 'Ndimi models (custom HTTP)')], default='auto', max_length=12, verbose_name='Voice (text-to-speech)'),
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
