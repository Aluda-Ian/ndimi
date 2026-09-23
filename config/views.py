import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts.models import needs_email_verification

USE_TOOL = 'system.use_dubbing_tool'


def landing(request):
    """Public marketing page. Also shown at / to visitors who aren't signed in."""
    return FileResponse(open(Path(settings.BASE_DIR) / 'landing.html', 'rb'), content_type='text/html')


@ensure_csrf_cookie
def home(request):
    if not request.user.is_authenticated:
        return landing(request)
    if not request.user.has_perm(USE_TOOL):
        return render(request, 'accounts/no_access.html', status=403)
    if needs_email_verification(request.user):
        return render(request, 'accounts/verify_required.html', status=403)
    return FileResponse(open(Path(settings.BASE_DIR) / 'index.html', 'rb'), content_type='text/html')


def admin_home(request):
    """/admin/ opens the in-app Settings dashboard; ?classic=1 shows Django's admin index."""
    from django.contrib import admin
    from django.shortcuts import redirect

    if request.GET.get('classic') or not (request.user.is_authenticated and request.user.is_staff):
        return admin.site.admin_view(admin.site.index)(request)  # keeps Django's staff-only check
    return redirect('/#settings')


def admin_login(request):
    """Use the app's sign-in page for the admin too."""
    from django.shortcuts import redirect
    from django.utils.http import urlencode

    target = request.GET.get('next', '/#settings')
    if target.rstrip('/') == '/admin':
        target = '/#settings'
    return redirect('/accounts/login/?' + urlencode({'next': target}))


@login_required
def protected_media(request, path):
    """Serve uploaded media to users who can use the dubbing tool."""
    if not request.user.has_perm(USE_TOOL) or needs_email_verification(request.user):
        raise Http404('File not found.')
    if path.startswith('dubbing/'):
        raise Http404('File not found.')  # dub files are served by /api/dubs/ with ownership checks
    root = Path(settings.MEDIA_ROOT).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise Http404('File not found.')
    content_type, _ = mimetypes.guess_type(target.name)
    return FileResponse(open(target, 'rb'), content_type=content_type or 'application/octet-stream')
