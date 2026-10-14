from django.db import migrations, models


def move_resend_into_email_settings(apps, schema_editor):
    """Resend now lives on the Email settings page. Carry over a key saved in Integrations, then
    remove the separate Resend/Brevo integration rows added in 0006."""
    Integration = apps.get_model('system', 'Integration')
    EmailSettings = apps.get_model('system', 'EmailSettings')
    resend = Integration.objects.filter(slug='resend').first()
    if resend and resend.enabled and resend.api_key_encrypted:
        cfg, _ = EmailSettings.objects.get_or_create(pk=1)
        cfg.provider = 'resend'
        cfg.resend_api_key_encrypted = resend.api_key_encrypted  # same encryption key, copied as is
        cfg.save(update_fields=['provider', 'resend_api_key_encrypted'])
    Integration.objects.filter(slug__in=['resend', 'brevo']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('system', '0006_email_api_integrations'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='emailsettings',
            options={'verbose_name': 'email settings', 'verbose_name_plural': 'email settings'},
        ),
        migrations.AddField(
            model_name='emailsettings',
            name='provider',
            field=models.CharField(
                choices=[('resend', 'Resend (HTTPS, works on Railway)'), ('smtp', 'SMTP server (blocked on Railway Hobby)')],
                default='smtp', max_length=8, verbose_name='Send through',
                help_text='Resend sends over HTTPS. Use it on Railway, which blocks SMTP on the Hobby plan.',
            ),
        ),
        migrations.AddField(
            model_name='emailsettings',
            name='resend_api_key_encrypted',
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.RunPython(move_resend_into_email_settings, migrations.RunPython.noop),
    ]
