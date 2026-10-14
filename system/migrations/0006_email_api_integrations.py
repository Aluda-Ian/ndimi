from django.db import migrations


def add_email_apis(apps, schema_editor):
    Integration = apps.get_model('system', 'Integration')
    Integration.objects.get_or_create(
        slug='resend',
        defaults={
            'name': 'Resend (email over HTTPS)',
            'category': 'messaging',
            'base_url': 'https://api.resend.com',
            'docs_url': 'https://resend.com/docs',
            'key_label': 'API key',
            'notes': ('Sends password resets and other emails over HTTPS, for hosts that block SMTP (Railway Hobby). '
                      'Verify your sending domain at resend.com > Domains, then set the From address in Settings > Email '
                      'to an address on that domain. When this is on, it is used instead of the SMTP settings.'),
        },
    )
    Integration.objects.get_or_create(
        slug='brevo',
        defaults={
            'name': 'Brevo (email over HTTPS)',
            'category': 'messaging',
            'base_url': 'https://api.brevo.com/v3',
            'docs_url': 'https://developers.brevo.com',
            'key_label': 'API key',
            'notes': ('Alternative to Resend. Create an API key (not an SMTP key) at Brevo > SMTP & API > API keys and '
                      'authenticate your sender domain. Used only if Resend is not turned on.'),
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('system', '0005_roadmap_seed'),
    ]

    operations = [
        migrations.RunPython(add_email_apis, migrations.RunPython.noop),
    ]
