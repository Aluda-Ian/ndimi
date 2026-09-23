from django.db import DatabaseError
from django.http import JsonResponse
from django.shortcuts import render

EXEMPT_PREFIXES = ('/admin/', '/accounts/', '/api/health/', '/api/manage/', '/static/')


class MaintenanceModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(EXEMPT_PREFIXES):
            from .models import SystemSettings

            try:
                site = SystemSettings.load()
            except DatabaseError:
                site = None
            if site and site.maintenance_mode and not request.user.has_perm('system.bypass_maintenance'):
                if request.path.startswith('/api/'):
                    return JsonResponse({'error': site.maintenance_message or 'Down for maintenance.'}, status=503)
                return render(request, 'system/maintenance.html', {'site': site}, status=503)
        return self.get_response(request)
