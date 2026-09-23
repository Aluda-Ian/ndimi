import json

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models

from . import crypto


class SingletonModel(models.Model):
    """A table with exactly one row (pk=1)."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AccessRight(models.Model):
    """Holds app-wide permissions. No database table; appears only in the permission lists."""

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = 'app access'
        permissions = [
            ('use_dubbing_tool', 'Can use the dubbing tool'),
            ('view_all_projects', "Can see every user's projects"),
            ('bypass_maintenance', 'Can use the app during maintenance'),
        ]


class SystemSettings(SingletonModel):
    site_name = models.CharField(max_length=80, default='Ndimi')
    support_email = models.EmailField(blank=True, help_text='Shown to users on error and maintenance pages.')
    allow_signup = models.BooleanField(default=True, help_text='Let people create their own accounts.')
    require_email_verification = models.BooleanField(
        default=False,
        help_text='Users must confirm their email before using the dubbing tool. Superusers are exempt.',
    )
    default_group = models.ForeignKey(
        Group, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        help_text='Group new sign-ups join. It decides what they can do.',
    )
    maintenance_mode = models.BooleanField(
        default=False,
        help_text='Only people with "Can use the app during maintenance" can use the app. The admin panel stays open.',
    )
    maintenance_message = models.CharField(
        max_length=240, blank=True, default="We're updating Ndimi. Please check back soon.",
    )
    max_video_upload_mb = models.PositiveIntegerField(default=500, help_text='Largest video admins can upload in demo setup.')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        verbose_name = 'system settings'
        verbose_name_plural = 'system settings'

    def __str__(self):
        return 'System settings'


class EmailSettings(SingletonModel):
    SECURITY_CHOICES = [('tls', 'STARTTLS (usually port 587)'), ('ssl', 'SSL/TLS (usually port 465)'), ('none', 'None')]

    enabled = models.BooleanField(
        default=False,
        help_text='Off: emails (like password resets) are printed in the server console instead of sent.',
    )
    host = models.CharField('SMTP host', max_length=255, blank=True, help_text='e.g. smtp.gmail.com, mail.jeotamedia.com')
    port = models.PositiveIntegerField(default=587)
    security = models.CharField(max_length=4, choices=SECURITY_CHOICES, default='tls')
    username = models.CharField(max_length=255, blank=True)
    password_encrypted = models.TextField(blank=True, editable=False)
    from_email = models.EmailField('From address', blank=True, help_text='e.g. no-reply@jeotamedia.com')
    from_name = models.CharField('From name', max_length=80, blank=True, default='Ndimi')
    timeout = models.PositiveIntegerField('Timeout (seconds)', default=20)
    last_test_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_test_result = models.CharField(max_length=255, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        verbose_name = 'email (SMTP) settings'
        verbose_name_plural = 'email (SMTP) settings'

    def __str__(self):
        return 'Email (SMTP) settings'

    @property
    def password(self):
        return crypto.decrypt(self.password_encrypted)

    @password.setter
    def password(self, value):
        self.password_encrypted = crypto.encrypt(value)

    @property
    def formatted_from(self):
        if not self.from_email:
            return ''
        return f'{self.from_name} <{self.from_email}>' if self.from_name else self.from_email

    def smtp_connection(self, fail_silently=False):
        from django.core.mail.backends.smtp import EmailBackend

        return EmailBackend(
            host=self.host,
            port=self.port,
            username=self.username or None,
            password=self.password or None,
            use_tls=self.security == 'tls',
            use_ssl=self.security == 'ssl',
            timeout=self.timeout,
            fail_silently=fail_silently,
        )


class Integration(models.Model):
    CATEGORY_CHOICES = [
        ('ai', 'AI speech & translation'),
        ('data', 'Language data'),
        ('storage', 'Storage'),
        ('payments', 'Payments'),
        ('messaging', 'Messaging'),
        ('other', 'Other'),
    ]
    ENVIRONMENT_CHOICES = [('sandbox', 'Sandbox / test'), ('live', 'Live')]

    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=40, unique=True, help_text='Code name the app uses to find this integration, e.g. openai.')
    category = models.CharField(max_length=12, choices=CATEGORY_CHOICES, default='other')
    enabled = models.BooleanField(default=False)
    environment = models.CharField(max_length=8, choices=ENVIRONMENT_CHOICES, default='sandbox')
    base_url = models.URLField('Base URL', blank=True)
    docs_url = models.URLField('Docs', blank=True)
    key_label = models.CharField(max_length=60, blank=True, default='API key', help_text='What this service calls its key.')
    secret_label = models.CharField(max_length=60, blank=True, help_text='What this service calls its secret. Blank if it has none.')
    api_key_encrypted = models.TextField(blank=True, editable=False)
    api_secret_encrypted = models.TextField(blank=True, editable=False)
    extra_secrets_encrypted = models.TextField(blank=True, editable=False)
    config = models.JSONField(default=dict, blank=True, help_text='Settings that are not secret, e.g. bucket, region, shortcode.')
    notes = models.TextField(blank=True, help_text='Setup notes for admins.')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['category', 'name']

    def __str__(self):
        return self.name

    @property
    def api_key(self):
        return crypto.decrypt(self.api_key_encrypted)

    @api_key.setter
    def api_key(self, value):
        self.api_key_encrypted = crypto.encrypt(value)

    @property
    def api_secret(self):
        return crypto.decrypt(self.api_secret_encrypted)

    @api_secret.setter
    def api_secret(self, value):
        self.api_secret_encrypted = crypto.encrypt(value)

    @property
    def extra_secrets(self):
        raw = crypto.decrypt(self.extra_secrets_encrypted)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    @extra_secrets.setter
    def extra_secrets(self, value):
        self.extra_secrets_encrypted = crypto.encrypt(json.dumps(value)) if value else ''

    @classmethod
    def credentials(cls, slug):
        """For app code: everything needed to call a service, or None if it's off.

        Example: creds = Integration.credentials('openai'); creds['api_key']
        """
        integration = cls.objects.filter(slug=slug, enabled=True).first()
        if integration is None:
            return None
        return {
            'base_url': integration.base_url,
            'environment': integration.environment,
            'api_key': integration.api_key or '',
            'api_secret': integration.api_secret or '',
            'secrets': integration.extra_secrets,
            'config': integration.config or {},
        }
