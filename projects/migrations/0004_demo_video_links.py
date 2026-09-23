from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0003_add_luhya'),
    ]

    operations = [
        migrations.AddField(
            model_name='demosetup',
            name='original_url',
            field=models.URLField(blank=True, help_text='YouTube link to the original video. Used instead of the uploaded file when set.', verbose_name='Original video link'),
        ),
        migrations.AddField(
            model_name='demosetup',
            name='dubbed_url',
            field=models.URLField(blank=True, help_text='YouTube link to the dubbed video. Used instead of the uploaded file when set.', verbose_name='Dubbed video link'),
        ),
    ]
