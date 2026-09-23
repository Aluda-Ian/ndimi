from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from .views import admin_home, admin_login, home, landing, protected_media

urlpatterns = [
    path('', home, name='home'),
    path('welcome/', landing, name='landing'),
    path('accounts/', include('accounts.urls')),
    # Shows "Forgotten your password?" on the admin login page.
    path('admin/password_reset/', RedirectView.as_view(pattern_name='password_reset'), name='admin_password_reset'),
    path('admin/', admin_home, name='admin-home'),
    path('admin/login/', admin_login, name='admin-login-redirect'),
    path('admin/', admin.site.urls),
    path('api/dubs/', include('dubbing.urls')),
    path('api/manage/', include('system.manage_urls')),
    path('api/', include('projects.urls')),
    path('media/<path:path>', protected_media, name='protected-media'),
]
