"""Run with:  python manage.py test dubbing

Pushes a generated test video through the full Ndimi pipeline using fake
providers (no API keys or network needed) and real ffmpeg.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock, skipUnless

import numpy as np
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .engine.audio import SR, FFmpeg
from .engine.segmenter import build_lines
from .engine.timing import fit
from .engine.types import Transcript, Word
from .models import DubbingSettings, DubJob
from .worker import run_once

HAS_FFMPEG = shutil.which('ffmpeg') is not None
MEDIA = tempfile.mkdtemp(prefix='ndimi-test-media-')


class FakeSTT:
    def transcribe(self, audio_path, language=None, num_speakers=None):
        words = [Word(0.5, 0.9, 'Hello'), Word(0.95, 1.4, 'there.'), Word(1.5, 2.4, 'Welcome.'),
                 Word(3.5, 4.0, 'Karibu', 'speaker_1'), Word(4.1, 5.0, 'friends.', 'speaker_1')]
        return Transcript(language='eng', words=words)


class FakeTranslator:
    def translate(self, lines, source, target, glossary=None):
        return {l['i']: f'[{target}] {l["text"]}' for l in lines}


class FakeTTS:
    def clone_voice(self, name, sample_paths, language=None):
        assert Path(sample_paths[0]).exists()
        return 'cloned-' + name[-9:]

    def delete_voice(self, voice_id):
        pass

    def synthesize(self, text, voice_id, out_path, **kwargs):
        seconds = 0.08 * len(text)
        t = np.arange(int(seconds * SR)) / SR
        tone = (0.3 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
        wav = out_path + '.wav'
        FFmpeg().save(np.concatenate([np.zeros(SR // 5, np.float32), tone]), wav)
        FFmpeg().run(['-i', wav, out_path])
        Path(wav).unlink()
        return out_path


def make_video(path, seconds=6):
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', f'testsrc=size=320x240:rate=25:duration={seconds}',
                    '-f', 'lavfi', '-i', f'sine=frequency=220:duration={seconds}', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', path], check=True, capture_output=True)


class EngineTests(TestCase):
    def test_segmenter_splits_on_speaker_and_pause(self):
        lines = build_lines(FakeSTT().transcribe('x'))
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1].speaker, 'speaker_1')

    def test_fit_speeds_up_within_limit(self):
        speed, overflow = fit(3.0, 0, 2, 2.5, max_speedup=1.3)
        self.assertAlmostEqual(speed, 1.3)
        self.assertGreater(overflow, 0)
        self.assertEqual(fit(1.0, 0, 2, 3), (1.0, 0.0))


@skipUnless(HAS_FFMPEG, 'ffmpeg is not installed')
@override_settings(MEDIA_ROOT=MEDIA)
class PipelineTests(TestCase):
    def setUp(self):
        from projects.models import Language

        Language.objects.update_or_create(code='luo', defaults={'name': 'Dholuo', 'available': True, 'enabled': True})
        self.user = get_user_model().objects.create_user('tester', 'tester@example.com', 'pass-12345!')
        self.user.user_permissions.add(
            Permission.objects.get(codename='use_dubbing_tool'),
            Permission.objects.get(codename='add_dubjob'),
        )
        cfg = DubbingSettings.load()
        cfg.separation = 'duck'
        cfg.default_engine = 'pipeline'
        cfg.default_voice_id = 'default-voice'  # speakers in the test clip talk too briefly to clone
        cfg.save()
        self.client.force_login(self.user)
        self.patches = [
            mock.patch('dubbing.pipeline.get_stt', return_value=FakeSTT()),
            mock.patch('dubbing.pipeline.get_translator', return_value=FakeTranslator()),
            mock.patch('dubbing.pipeline.get_tts', return_value=FakeTTS()),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _create(self, review):
        path = Path(MEDIA) / 'in.mp4'
        make_video(str(path))
        upload = SimpleUploadedFile('clip.mp4', path.read_bytes(), content_type='video/mp4')
        response = self.client.post('/api/dubs/', {'file': upload, 'target_language': 'luo', 'review': str(review).lower()})
        self.assertEqual(response.status_code, 201, response.content)
        return DubJob.objects.get(pk=response.json()['dub']['id'])

    def test_full_pipeline_with_review_and_edit(self):
        job = self._create(review=True)
        self.assertTrue(run_once('test'))
        job.refresh_from_db()
        self.assertEqual(job.status, 'review', job.error)
        self.assertEqual(job.source_language, 'en')
        self.assertEqual(job.segments.count(), 2)
        self.assertEqual(job.speakers.count(), 2)

        # Edit a line during review, then approve.
        r = self.client.patch(f'/api/dubs/{job.pk}/segments/0/', {'translated_text': 'Amosi'}, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.client.post(f'/api/dubs/{job.pk}/approve/').status_code, 200)
        self.assertTrue(run_once('test'))
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed', job.error)
        self.assertTrue(job.dubbed_video and Path(job.dubbed_video.path).exists())
        self.assertFalse(job.speakers.filter(voice_id='').exists())
        info = FFmpeg().info(job.dubbed_video.path)
        self.assertAlmostEqual(info['duration'], 6.0, delta=0.3)

        # Edit after completion -> regenerate just that line.
        self.client.patch(f'/api/dubs/{job.pk}/segments/1/', {'translated_text': 'Oruaki mathoth'}, content_type='application/json')
        self.assertEqual(job.segments.get(index=1).status, 'stale')
        self.assertEqual(self.client.post(f'/api/dubs/{job.pk}/regenerate/', {}, content_type='application/json').status_code, 200)
        self.assertTrue(run_once('test'))
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed', job.error)
        self.assertFalse(job.segments.exclude(status='ready').exists())

        # Downloads are private to the owner.
        self.assertEqual(self.client.get(f'/api/dubs/{job.pk}/download/video/').status_code, 200)
        other = get_user_model().objects.create_user('other', 'o@example.com', 'pass-12345!')
        other.user_permissions.add(Permission.objects.get(codename='use_dubbing_tool'))
        self.client.force_login(other)
        self.assertEqual(self.client.get(f'/api/dubs/{job.pk}/download/video/').status_code, 404)

    def test_provider_failure_retries_then_fails(self):
        from .providers import ProviderError

        job = self._create(review=False)
        with mock.patch('dubbing.pipeline.get_stt') as stt:
            stt.return_value.transcribe.side_effect = ProviderError('rate limited', retryable=True)
            run_once('test')
        job.refresh_from_db()
        self.assertEqual(job.status, 'queued')  # scheduled retry
        self.assertIsNotNone(job.run_after)
