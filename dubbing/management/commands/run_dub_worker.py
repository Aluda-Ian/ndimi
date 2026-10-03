import logging
import threading
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from dubbing.engine.audio import FFmpeg
from dubbing.models import DubbingSettings
from dubbing.worker import record_worker, recover_stale, run_once, worker_name


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
        record_worker(name, version)
        self.stdout.write(self.style.SUCCESS(f'Dub worker {name} started. Press Ctrl+C to stop.'))
        stop_heartbeat = threading.Event()

        def heartbeat():
            while not stop_heartbeat.wait(15):
                close_old_connections()
                try:
                    record_worker(name, version)
                except Exception:
                    logging.exception('Could not record heartbeat for worker %s', name)
                finally:
                    close_old_connections()

        heartbeat_thread = threading.Thread(target=heartbeat, name=f'{name}-heartbeat', daemon=True)
        heartbeat_thread.start()
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
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=2)
            close_old_connections()
            try:
                record_worker(name, version, running=False)
            except Exception:
                logging.exception('Could not mark worker %s as stopped', name)
