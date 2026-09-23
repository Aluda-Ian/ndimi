from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    # Sign up, in, out
    path('signup/', views.signup, name='signup'),
    path('login/', views.SignInView.as_view(), name='login'),
    path('logout/', views.sign_out, name='logout'),

    # Account
    path('', views.account, name='account'),
    path('verify/resend/', views.resend_verification, name='resend_verification'),
    path('verify/<str:token>/', views.verify_email, name='verify_email'),
    path('sessions/sign-out-others/', views.sign_out_other_devices, name='sign_out_other_devices'),
    path('delete/', views.delete_account, name='delete_account'),

    # Password change (signed in)
    path('password/', views.PasswordChangeView.as_view(), name='password_change'),

    # Password reset (forgotten)
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='accounts/password_reset_form.html',
            email_template_name='accounts/password_reset_email.txt',
            subject_template_name='accounts/password_reset_subject.txt',
        ),
        name='password_reset',
    ),
    path(
        'password-reset/sent/',
        auth_views.PasswordResetDoneView.as_view(template_name='accounts/password_reset_done.html'),
        name='password_reset_done',
    ),
    path('reset/<uidb64>/<token>/', views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(template_name='accounts/password_reset_complete.html'),
        name='password_reset_complete',
    ),
]
