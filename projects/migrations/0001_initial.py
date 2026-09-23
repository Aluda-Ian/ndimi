import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Language',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.SlugField(help_text='Short code, e.g. sw, ki, luo.', max_length=12, unique=True)),
                ('name', models.CharField(max_length=80)),
                ('note', models.CharField(blank=True, help_text='Small label under the name, e.g. "Available in preview".', max_length=120)),
                ('available', models.BooleanField(default=False, help_text='On: users can run the full dub flow. Off: users see a "coming soon" screen.')),
                ('enabled', models.BooleanField(default=True, help_text='Off hides the language from users entirely.')),
                ('sort_order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'ordering': ['sort_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='DemoSetup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('original_video', models.FileField(blank=True, help_text='Original English video.', upload_to='demo/', validators=[django.core.validators.FileExtensionValidator(['mp4', 'webm', 'mov', 'm4v'])])),
                ('dubbed_video', models.FileField(blank=True, help_text='Dubbed Kiswahili video.', upload_to='demo/', validators=[django.core.validators.FileExtensionValidator(['mp4', 'webm', 'mov', 'm4v'])])),
                ('script', models.TextField(blank=True, help_text='One line per segment: 0:00 | Narrator | English line | Kiswahili line')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'demo setup',
                'verbose_name_plural': 'demo setup',
            },
        ),
    ]
