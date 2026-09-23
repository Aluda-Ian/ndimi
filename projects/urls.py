from django.urls import path

from . import views

urlpatterns = [
    path('health/', views.health, name='health'),
    path('config/', views.app_config, name='app-config'),
    path('config/setup/', views.update_setup, name='update-setup'),
    path('projects/', views.projects, name='projects'),
]
