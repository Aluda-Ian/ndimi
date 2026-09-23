LANGUAGE_NAMES = {
    'en': 'English', 'sw': 'Kiswahili (Swahili)', 'ki': 'Gĩkũyũ (Kikuyu)', 'luo': 'Dholuo (Luo)',
    'kln': 'Kalenjin', 'mas': 'Maa (Maasai)', 'so': 'Somali', 'mer': 'Kimeru (Meru)', 'kam': 'Kikamba (Kamba)',
    'guz': 'Ekegusii (Kisii)', 'luy': 'Oluluhya (Luhya)', 'fr': 'French', 'ar': 'Arabic',
}

# Rough spoken characters per second, used to size translations to their time slot.
CHARS_PER_SECOND = {'en': 15, 'sw': 14, 'default': 14}


def language_name(code):
    if not code:
        return 'the source language'
    try:
        from projects.models import Language

        lang = Language.objects.filter(code=code).first()
        if lang:
            return lang.name
    except Exception:  # outside Django or before migrations
        pass
    return LANGUAGE_NAMES.get(code, code)


def char_budget(duration, language):
    return max(8, int(duration * CHARS_PER_SECOND.get(language, CHARS_PER_SECOND['default'])))
