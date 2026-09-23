import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AccessRight',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ],
            options={
                'verbose_name': 'app access',
                'managed': False,
                'default_permissions': (),
                'permissions': [
                    ('use_dubbing_tool', 'Can use the dubbing tool'),
                    ('view_all_projects', "Can see every user's projects"),
                    ('bypass_maintenance', 'Can use the app during maintenance'),
                ],
            },
        ),
        migrations.CreateModel(
            name='SystemSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('site_name', models.CharField(default='Ndimi', max_length=80)),
                ('support_email', models.EmailField(blank=True, help_text='Shown to users on error and maintenance pages.', max_length=254)),
                ('allow_signup', models.BooleanField(default=True, help_text='Let people create their own accounts.')),
                ('maintenance_mode', models.BooleanField(default=False, help_text='Only people with "Can use the app during maintenance" can use the app. The admin panel stays open.')),
                ('maintenance_message', models.CharField(blank=True, default="We're updating Ndimi. Please check back soon.", max_length=240)),
                ('max_video_upload_mb', models.PositiveIntegerField(default=500, help_text='Largest video admins can upload in demo setup.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('default_group', models.ForeignKey(blank=True, help_text='Group new sign-ups join. It decides what they can do.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='auth.group')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'system settings',
                'verbose_name_plural': 'system settings',
            },
        ),
        migrations.CreateModel(
            name='EmailSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=False, help_text='Off: emails (like password resets) are printed in the server console instead of sent.')),
                ('host', models.CharField(blank=True, help_text='e.g. smtp.gmail.com, mail.jeotamedia.com', max_length=255, verbose_name='SMTP host')),
                ('port', models.PositiveIntegerField(default=587)),
                ('security', models.CharField(choices=[('tls', 'STARTTLS (usually port 587)'), ('ssl', 'SSL/TLS (usually port 465)'), ('none', 'None')], default='tls', max_length=4)),
                ('username', models.CharField(blank=True, max_length=255)),
                ('password_encrypted', models.TextField(blank=True, editable=False)),
                ('from_email', models.EmailField(blank=True, help_text='e.g. no-reply@jeotamedia.com', max_length=254, verbose_name='From address')),
                ('from_name', models.CharField(blank=True, default='Ndimi', max_length=80, verbose_name='From name')),
                ('timeout', models.PositiveIntegerField(default=20, verbose_name='Timeout (seconds)')),
                ('last_test_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('last_test_result', models.CharField(blank=True, editable=False, max_length=255)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'email (SMTP) settings',
                'verbose_name_plural': 'email (SMTP) settings',
            },
        ),
        migrations.CreateModel(
            name='Integration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)),
                ('slug', models.SlugField(help_text='Code name the app uses to find this integration, e.g. openai.', max_length=40, unique=True)),
                ('category', models.CharField(choices=[('ai', 'AI speech & translation'), ('data', 'Language data'), ('storage', 'Storage'), ('payments', 'Payments'), ('messaging', 'Messaging'), ('other', 'Other')], default='other', max_length=12)),
                ('enabled', models.BooleanField(default=False)),
                ('environment', models.CharField(choices=[('sandbox', 'Sandbox / test'), ('live', 'Live')], default='sandbox', max_length=8)),
                ('base_url', models.URLField(blank=True, verbose_name='Base URL')),
                ('docs_url', models.URLField(blank=True, verbose_name='Docs')),
                ('key_label', models.CharField(blank=True, default='API key', help_text='What this service calls its key.', max_length=60)),
                ('secret_label', models.CharField(blank=True, help_text='What this service calls its secret. Blank if it has none.', max_length=60)),
                ('api_key_encrypted', models.TextField(blank=True, editable=False)),
                ('api_secret_encrypted', models.TextField(blank=True, editable=False)),
                ('extra_secrets_encrypted', models.TextField(blank=True, editable=False)),
                ('config', models.JSONField(blank=True, default=dict, help_text='Settings that are not secret, e.g. bucket, region, shortcode.')),
                ('notes', models.TextField(blank=True, help_text='Setup notes for admins.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['category', 'name'],
            },
        ),
    ]
