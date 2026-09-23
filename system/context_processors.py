from django.db import DatabaseError


def site_settings(request):
    from .models import SystemSettings

    try:
        return {'site_settings': SystemSettings.load()}
    except DatabaseError:
        return {'site_settings': None}
