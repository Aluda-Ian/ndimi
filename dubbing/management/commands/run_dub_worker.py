import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from dubbing.engine.audio import FFmpeg
from dubbing.models import DubbingSettings
from dubbing.worker import recover_stale, run_once, worker_name


class Command(BaseCommand):
    help = 'Process dubbing jobs from the queue. Run one or more of these next to the web server.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process queued jobs, then exit.')
        parser.add_argument('--interval', type=float, default=3.0, help='Seconds between queue checks when idle.')
        parser.add_argument('--name', default='', help='Worker name shown in the admin.')

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
        name = options['name'] or worker_name()
        version = FFmpeg(DubbingSettings.load().ffmpeg_path).check()
        if not version:
            self.stderr.write(self.style.WARNING('ffmpeg was not found. Install it or set its path in Admin > Dubbing settings.'))
        else:
            self.stdout.write(version)
        self.stdout.write(self.style.SUCCESS(f'Dub worker {name} started. Press Ctrl+C to stop.'))
        last_recover = 0.0
        try:
            while True:
                close_old_connections()
                if time.time() - last_recover > 300:
                    recover_stale()
                    last_recover = time.time()
                worked = run_once(name)
                if options['once'] and not worked:
                    break
                if not worked:
                    time.sleep(options['interval'])
        except KeyboardInterrupt:
            self.stdout.write('Stopping worker.')
