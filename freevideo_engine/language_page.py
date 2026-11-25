"""The interface language from the page's settings, independent of the HTTP server.

One setting for the launcher and the page (saved with the launcher's settings): 'auto' follows the system language,
otherwise a language's tag. The page reads it with the languages it can offer and the one each choice shows.
"""
from .i18n import interface_language, interface_languages
from .launcher_settings import Store, default_language

AUTO = 'auto'


class InvalidRequest(ValueError):
    def __init__(self, field, allowed, got):
        super().__init__(field)
        self.field, self.allowed, self.got = field, allowed, got


def read(engine):
    """{setting, language, system, languages}: the saved choice ('auto' or a tag; an older saved value such as 'zh' or
    'zh_cn' reads as the tag it shows), the language it shows, what 'auto' shows on this computer, and every language
    ready to offer as {tag, name, dir}, English and Simplified Chinese included."""
    languages = interface_languages()
    saved = Store(engine).read().get('language')
    saved = saved.strip() if isinstance(saved, str) else ''
    if not saved or saved.lower() == AUTO:
        setting = AUTO
    else:
        setting = saved if saved in languages else interface_language(saved, default_language)
    return dict(setting=setting, language=interface_language(setting, default_language),
                system=interface_language(AUTO, default_language),
                languages=[dict(tag=tag, **meta) for tag, meta in sorted(languages.items())])


def apply(engine, body):
    """Save {language: 'auto' or a tag of a language ready to offer}; returns read()."""
    if not isinstance(body, dict):
        raise InvalidRequest('', 'an object', None)
    for field in body:
        if field != 'language':
            raise InvalidRequest(field, 'absent (unknown field)', None)
    value = body.get('language')
    offered = interface_languages()
    if not isinstance(value, str) or (value != AUTO and value not in offered):
        raise InvalidRequest('language', 'one of ' + ', '.join([AUTO, *sorted(offered)]),
                             value if isinstance(value, str) and len(value) <= 40 else None)
    Store(engine).save_language(value)
    return read(engine)
