from django.db import migrations

DEFAULTS = [
    ('sw', 'Kiswahili', 'Available in preview', True),
    ('ki', 'Gĩkũyũ', '', False),
    ('luo', 'Dholuo', '', False),
    ('kln', 'Kalenjin', '', False),
    ('mas', 'Maa (Maasai)', '', False),
    ('so', 'Somali', '', False),
]


def seed(apps, schema_editor):
    Language = apps.get_model('projects', 'Language')
    for order, (code, name, note, available) in enumerate(DEFAULTS):
        Language.objects.get_or_create(
            code=code,
            defaults={'name': name, 'note': note, 'available': available, 'sort_order': order},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
