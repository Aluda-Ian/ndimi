from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('system', '0002_seed'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='require_email_verification',
            field=models.BooleanField(default=False, help_text='Users must confirm their email before using the dubbing tool. Superusers are exempt.'),
        ),
    ]
