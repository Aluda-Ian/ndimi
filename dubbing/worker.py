import logging
import os
import socket
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import DubJob
from .pipeline import run_job

logger = logging.getLogger(__name__)
STALE_AFTER = timedelta(hours=2)


def worker_name():
    return f'{socket.gethostname()}:{os.getpid()}'


def recover_stale():
    """Jobs whose worker died (locked for too long) go back in the queue."""
    cutoff = timezone.now() - STALE_AFTER
    count = DubJob.objects.filter(status='running', locked_at__lt=cutoff).update(status='queued', locked_by='', locked_at=None)
    if count:
        logger.warning('Requeued %s stale dub job(s)', count)


def claim(name):
    now = timezone.now()
    candidates = (DubJob.objects.filter(status='queued', cancel_requested=False)
                  .filter(Q(run_after__isnull=True) | Q(run_after__lte=now))
                  .order_by('created_at').values_list('pk', flat=True)[:10])
    for pk in candidates:
        # Atomic compare-and-set: only one worker wins each job.
        if DubJob.objects.filter(pk=pk, status='queued').update(status='running', locked_by=name, locked_at=now, run_after=None):
            return DubJob.objects.get(pk=pk)
    # Cancelled while queued
    DubJob.objects.filter(status='queued', cancel_requested=True).update(status='cancelled', finished_at=now)
    return None


def run_once(name=None):
    job = claim(name or worker_name())
    if job is None:
        return False
    logger.info('Running dub job %s (%s)', job.pk, job.name)
    run_job(job)
    return True
