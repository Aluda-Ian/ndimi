from django.urls import path

from . import manage_api as api

urlpatterns = [
    path('', api.index, name='manage-index'),
    path('email/test/', api.email_test, name='manage-email-test'),
    path('users/<int:pk>/password/', api.set_password, name='manage-set-password'),
    path('<slug:key>/', api.section, name='manage-section'),
    path('<slug:key>/new/', api.new_item, name='manage-new'),
    path('<slug:key>/actions/', api.run_action, name='manage-action'),
    path('<slug:key>/<str:pk>/', api.item, name='manage-item'),
]
