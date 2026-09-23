from django.db import migrations


def add_luhya(apps, schema_editor):
    Language = apps.get_model('projects', 'Language')
    last = Language.objects.order_by('-sort_order').first()
    Language.objects.get_or_create(
        code='luy',
        defaults={
            'name': 'Oluluhya (Luhya)',
            'note': '',
            'available': False,
            'sort_order': (last.sort_order + 1) if last else 0,
        },
    )


def remove_luhya(apps, schema_editor):
    apps.get_model('projects', 'Language').objects.filter(code='luy').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0002_default_languages'),
    ]

    operations = [
        migrations.RunPython(add_luhya, remove_luhya),
    ]
