from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import PasswordResetForm

from .models import is_email_verified, mark_email_verified

User = get_user_model()
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'email_confirmed', 'group_list', 'is_staff', 'is_active', 'last_login')
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'groups')
    actions = ['send_password_reset', 'confirm_emails', 'deactivate']

    @admin.display(description='Email confirmed', boolean=True)
    def email_confirmed(self, obj):
        return is_email_verified(obj)

    @admin.action(description='Send a password reset email', permissions=['change'])
    def send_password_reset(self, request, queryset):
        sent = skipped = 0
        for user in queryset:
            if not user.email or not user.is_active:
                skipped += 1
                continue
            form = PasswordResetForm({'email': user.email})
            if form.is_valid():
                form.save(
                    request=request,
                    use_https=request.is_secure(),
                    email_template_name='accounts/password_reset_email.txt',
                    subject_template_name='accounts/password_reset_subject.txt',
                )
                sent += 1
        self.message_user(request, f'Reset emails sent: {sent}. Skipped (no email or inactive): {skipped}.')

    @admin.action(description='Mark email as confirmed', permissions=['change'])
    def confirm_emails(self, request, queryset):
        count = 0
        for user in queryset.exclude(email=''):
            mark_email_verified(user)
            count += 1
        self.message_user(request, f'Marked {count} email(s) as confirmed.')

    @admin.action(description='Deactivate (block sign-in)', permissions=['change'])
    def deactivate(self, request, queryset):
        queryset = queryset.exclude(pk=request.user.pk)
        if not request.user.is_superuser:
            queryset = queryset.exclude(is_superuser=True)
        count = queryset.update(is_active=False)
        self.message_user(request, f'Deactivated {count} account(s). They can no longer sign in.', messages.WARNING)

    @admin.display(description='Groups')
    def group_list(self, obj):
        return ', '.join(g.name for g in obj.groups.all()) or '—'

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('groups')

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        # Only superusers can hand out superuser status.
        if not request.user.is_superuser:
            fields.append('is_superuser')
        return fields

    def has_change_permission(self, request, obj=None):
        # Only superusers can edit superuser accounts.
        if obj is not None and obj.is_superuser and not request.user.is_superuser:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_superuser and not request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)
