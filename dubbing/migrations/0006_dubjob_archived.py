from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dubbing', '0005_lipsync'),
    ]

    operations = [
        migrations.AddField(
            model_name='dubjob',
            name='archived',
            field=models.BooleanField(db_index=True, default=False, help_text='Hidden from the projects list; files are kept.'),
        ),
    ]
