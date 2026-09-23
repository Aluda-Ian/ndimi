from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.utils import timezone


def dashboard_stats(user):
    """Numbers for the admin dashboards. Each block only appears if the user may view it."""
    now = timezone.now()
    week = now - timedelta(days=7)
    data = {}
    try:
        if user.has_perm('dubbing.view_dubjob'):
            from dubbing.models import DubJob

            jobs = DubJob.objects.all()
            oldest = jobs.filter(status='queued', run_after__isnull=True).order_by('created_at').first()
            data['dubs'] = {
                'active': jobs.filter(status__in=['queued', 'running']).count(),
                'running': jobs.filter(status='running').count(),
                'review': jobs.filter(status='review').count(),
                'done_week': jobs.filter(status='completed', finished_at__gte=week).count(),
                'failed_week': jobs.filter(status='failed', finished_at__gte=week).count(),
                'recent': list(jobs.select_related('owner')[:6]),
                # Queued >2 min with nothing running usually means no worker is up.
                'worker_idle': bool(oldest and oldest.created_at < now - timedelta(minutes=2)
                                    and not jobs.filter(status='running').exists()),
            }
        if user.has_perm('auth.view_user'):
            users = get_user_model().objects.all()
            data['users'] = {
                'total': users.count(),
                'new_week': users.filter(date_joined__gte=week).count(),
                'active_week': users.filter(last_login__gte=week).count(),
            }
        if user.has_perm('system.view_integration'):
            from system.models import Integration

            data['integrations'] = {
                'enabled': Integration.objects.filter(enabled=True).count(),
                'total': Integration.objects.count(),
            }
        if user.has_perm('system.view_emailsettings'):
            from system.models import EmailSettings

            email = EmailSettings.load()
            data['email'] = {'enabled': email.enabled and bool(email.host), 'last_test': email.last_test_result}
        if user.has_perm('system.view_systemsettings'):
            from system.models import SystemSettings

            site = SystemSettings.load()
            data['site'] = {'maintenance': site.maintenance_mode, 'signup': site.allow_signup}
    except DatabaseError:
        pass
    return data
