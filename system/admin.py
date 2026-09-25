import json

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import LogEntry
from django.core.mail import EmailMessage
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from . import crypto
from .models import EmailSettings, Integration, Milestone, RoadmapTask, SystemSettings


class SingletonAdmin(admin.ModelAdmin):
    """Skip the list page: go straight to the single settings row."""

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = self.model.load()
        opts = self.model._meta
        return redirect(reverse(f'admin:{opts.app_label}_{opts.model_name}_change', args=[obj.pk]))

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(SystemSettings)
class SystemSettingsAdmin(SingletonAdmin):
    readonly_fields = ('updated_at', 'updated_by')
    fieldsets = (
        (None, {'fields': ('site_name', 'support_email')}),
        ('Sign-up', {'fields': ('allow_signup', 'default_group', 'require_email_verification')}),
        ('Maintenance', {'fields': ('maintenance_mode', 'maintenance_message')}),
        ('Uploads', {'fields': ('max_video_upload_mb',)}),
        ('History', {'fields': ('updated_at', 'updated_by')}),
    )


# ---------- email ----------

class EmailSettingsForm(forms.ModelForm):
    password = forms.CharField(
        required=False, strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
        help_text='Leave blank to keep the saved password.',
    )
    clear_password = forms.BooleanField(required=False, label='Remove saved password')

    class Meta:
        model = EmailSettings
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['password'].help_text = crypto.mask(self.instance.password) + ' Leave blank to keep it.'

    def clean(self):
        data = super().clean()
        if data.get('enabled') and not data.get('host'):
            self.add_error('host', 'Enter the SMTP host before turning email on.')
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get('clear_password'):
            obj.password = ''
        elif self.cleaned_data.get('password'):
            obj.password = self.cleaned_data['password']
        if commit:
            obj.save()
        return obj


@admin.register(EmailSettings)
class EmailSettingsAdmin(SingletonAdmin):
    form = EmailSettingsForm
    readonly_fields = ('last_test_at', 'last_test_result', 'updated_at', 'updated_by')
    fieldsets = (
        (None, {'fields': ('enabled',)}),
        ('Server', {'fields': ('host', 'port', 'security', 'timeout')}),
        ('Login', {'fields': ('username', 'password', 'clear_password')}),
        ('Sender', {'fields': ('from_name', 'from_email')}),
        ('Status', {'fields': ('last_test_at', 'last_test_result', 'updated_at', 'updated_by')}),
    )

    def response_change(self, request, obj):
        if '_send_test' in request.POST:
            self._send_test(request, obj)
            return redirect(request.path)
        return super().response_change(request, obj)

    def _send_test(self, request, obj):
        to = request.user.email
        if not to:
            self.message_user(request, 'Add an email address to your own account first, then send the test.', messages.ERROR)
            return
        if not obj.host:
            self.message_user(request, 'Enter the SMTP host first.', messages.ERROR)
            return
        message = EmailMessage(
            subject=f'Test email from {obj.from_name or "Ndimi"}',
            body='Your SMTP settings work. Password resets and other emails will be sent this way.',
            from_email=obj.formatted_from or None,
            to=[to],
            connection=obj.smtp_connection(),
        )
        try:
            message.send()
            result, level = f'Sent to {to}.', messages.SUCCESS
            if not obj.enabled:
                result += ' Email is still turned off. Tick "Enabled" to start sending.'
        except Exception as error:  # show any SMTP/network error to the admin
            result, level = f'Failed: {error}', messages.ERROR
        obj.last_test_at = timezone.now()
        obj.last_test_result = result[:255]
        obj.save(update_fields=['last_test_at', 'last_test_result'])
        self.message_user(request, f'Test email: {result}', level)


# ---------- integrations ----------

class IntegrationForm(forms.ModelForm):
    api_key = forms.CharField(
        required=False, strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'off'}),
    )
    clear_api_key = forms.BooleanField(required=False, label='Remove saved key')
    api_secret = forms.CharField(
        required=False, strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'off'}),
    )
    clear_api_secret = forms.BooleanField(required=False, label='Remove saved secret')
    extra_secrets = forms.CharField(
        required=False, label='Extra secrets (JSON)',
        widget=forms.Textarea(attrs={'rows': 3, 'autocomplete': 'off', 'spellcheck': 'false'}),
        help_text='Other secret values, e.g. {"passkey": "..."}. Never shown again after saving. Leave blank to keep what is saved.',
    )
    clear_extra_secrets = forms.BooleanField(required=False, label='Remove saved extra secrets')

    class Meta:
        model = Integration
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        inst = self.instance
        self.fields['api_key'].label = inst.key_label or 'API key'
        self.fields['api_secret'].label = inst.secret_label or 'API secret (optional)'
        if inst.pk:
            self.fields['api_key'].help_text = crypto.mask(inst.api_key) + ' Leave blank to keep it.'
            self.fields['api_secret'].help_text = crypto.mask(inst.api_secret) + ' Leave blank to keep it.'
            saved = inst.extra_secrets
            if saved:
                self.fields['extra_secrets'].help_text = (
                    f'Saved keys: {", ".join(sorted(saved))}. Pasting new JSON replaces all of them. '
                    'Leave blank to keep what is saved.'
                )

    def clean_extra_secrets(self):
        raw = self.cleaned_data.get('extra_secrets', '').strip()
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise forms.ValidationError(f'Not valid JSON: {error.msg}.')
        if not isinstance(value, dict):
            raise forms.ValidationError('Use a JSON object, e.g. {"passkey": "..."}.')
        return value

    def save(self, commit=True):
        obj = super().save(commit=False)
        data = self.cleaned_data
        if data.get('clear_api_key'):
            obj.api_key = ''
        elif data.get('api_key'):
            obj.api_key = data['api_key']
        if data.get('clear_api_secret'):
            obj.api_secret = ''
        elif data.get('api_secret'):
            obj.api_secret = data['api_secret']
        if data.get('clear_extra_secrets'):
            obj.extra_secrets = {}
        elif data.get('extra_secrets') is not None:
            obj.extra_secrets = data['extra_secrets']
        if commit:
            obj.save()
        return obj


@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    form = IntegrationForm
    list_display = ('name', 'category', 'environment', 'enabled', 'key_status', 'updated_at')
    list_filter = ('category', 'enabled', 'environment')
    list_editable = ('enabled',)
    search_fields = ('name', 'slug')
    readonly_fields = ('updated_at', 'updated_by')
    fieldsets = (
        (None, {'fields': ('name', 'slug', 'category', 'enabled', 'environment')}),
        ('Connection', {'fields': ('base_url', 'docs_url')}),
        ('Credentials', {
            'fields': ('api_key', 'clear_api_key', 'api_secret', 'clear_api_secret', 'extra_secrets', 'clear_extra_secrets'),
            'description': 'Stored encrypted. Saved values are never shown again.',
        }),
        ('Settings', {'fields': ('config', 'notes')}),
        ('Field labels', {'fields': ('key_label', 'secret_label'), 'classes': ('collapse',)}),
        ('History', {'fields': ('updated_at', 'updated_by')}),
    )

    @admin.display(description='Credentials')
    def key_status(self, obj):
        if obj.api_key is None:
            return 'Unreadable, enter again'
        return 'Saved' if obj.api_key_encrypted else 'Missing'

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# ---------- activity log ----------

@admin.register(LogEntry)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('action_time', 'user', 'content_type', 'object_repr', 'action', 'change_message')
    list_filter = ('action_flag', 'content_type')
    search_fields = ('object_repr', 'change_message', 'user__username')
    date_hierarchy = 'action_time'

    @admin.display(description='Action')
    def action(self, obj):
        return {1: 'Added', 2: 'Changed', 3: 'Deleted'}.get(obj.action_flag, '')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------- project roadmap ----------

@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'phase', 'progress', 'target_date', 'completed_at')
    list_filter = ('phase',)
    search_fields = ('code', 'title', 'goal')
    readonly_fields = ('completed_at',)
    fieldsets = (
        (None, {'fields': ('code', 'title', 'phase', 'goal')}),
        ('Planning', {'fields': ('order', 'target_date', 'completed_at')}),
    )

    @admin.display(description='Progress')
    def progress(self, obj):
        total = obj.tasks.count()
        return f'{obj.tasks.filter(done=True).count()} / {total}' if total else 'No tasks'


@admin.register(RoadmapTask)
class RoadmapTaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'milestone', 'area', 'owner', 'due_date', 'done')
    list_filter = ('done', 'area', 'milestone')
    search_fields = ('title', 'details', 'notes', 'owner', 'milestone__code', 'milestone__title')
    list_select_related = ('milestone',)
    readonly_fields = ('done_at', 'done_by', 'updated_at')
    fieldsets = (
        (None, {'fields': ('milestone', 'title', 'area', 'done')}),
        ('How to do it', {'fields': ('details', 'reference', 'command')}),
        ('Planning', {'fields': ('owner', 'due_date', 'order')}),
        ('Notes & history', {'fields': ('notes', 'done_at', 'done_by', 'updated_at')}),
    )

    def save_model(self, request, obj, form, change):
        # The form has already set obj.done; record who ticked it and when.
        was_done = bool(change and RoadmapTask.objects.filter(pk=obj.pk, done=True).exists())
        if obj.done != was_done:
            wanted, obj.done = obj.done, was_done
            obj.set_done(wanted, request.user)
        super().save_model(request, obj, form, change)
        obj.milestone.refresh_completion()
        if change and 'milestone' in form.changed_data and form.initial.get('milestone'):
            old = Milestone.objects.filter(pk=form.initial['milestone']).first()
            if old:
                old.refresh_completion()

    def delete_model(self, request, obj):
        milestone = obj.milestone
        super().delete_model(request, obj)
        milestone.refresh_completion()
