"""Interface languages on the Python side: which language files there are and which one a wanted language gets.

The rule is the one in web/i18n.js (negotiate); tests/i18n_negotiate_cases.json holds the cases both must pass.
A language file is web/locales/<tag>.json with a "_meta" entry ({"name": "繁體中文", "dir": "ltr"}); adding a language
is adding such a file. Nothing here reads settings or changes what is shown yet.
"""
import json
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / 'web' / 'locales'
TRADITIONAL_REGIONS = ('tw', 'hk', 'mo')
# Only ASCII spaces are taken off a tag, so this side and web/i18n.js agree (str.strip and String.trim differ).
ASCII_SPACE = ' \t\n\r\f'


def negotiate(requested, available, fallback='en'):
    """The language file for a list of wanted languages (most wanted first), from the tags there are. For each wanted
    language: the same tag; for Chinese, the script (Taiwan, Hong Kong and Macau read Traditional, everything else
    Simplified); a shorter form of the tag (es-419 -> es); then any file of the same language (pt-PT -> pt-BR). A single
    tag (the launcher's "zh-TW") counts as a list of one."""
    have = {tag.lower(): tag for tag in sorted(available)}
    if isinstance(requested, str):
        requested = [requested]
    for raw in requested or ():
        tag = str(raw if raw is not None else '').strip(ASCII_SPACE).replace('_', '-').lower()
        if not tag:
            continue
        if tag in have:
            return have[tag]
        parts = tag.split('-')
        if parts[0] == 'zh':
            traditional = 'hant' in parts or ('hans' not in parts and parts[-1] in TRADITIONAL_REGIONS)
            script = 'zh-hant' if traditional else 'zh-hans'
            if script in have:
                return have[script]
        for n in range(len(parts) - 1, 0, -1):
            shorter = '-'.join(parts[:n])
            if shorter in have:
                return have[shorter]
        same = next((key for key in have if key.split('-')[0] == parts[0]), None)
        if same:
            return have[same]
    return fallback


# The two languages written in the code (t(en, zh)); their files in web/locales are the collector's output and do not ship.
BUILT_IN = {'en': {'name': 'English', 'dir': 'ltr'}, 'zh-Hans': {'name': '简体中文', 'dir': 'ltr'}}


def interface_language(setting, system=None, folder=None):
    """The language the interface shows for a saved setting: 'auto' (or nothing) follows the system language, anything
    else is a wanted tag ('en', 'zh', a locale string an older version saved such as 'zh_cn', 'zh-TW'). English and
    Simplified Chinese are written in the code; any other language needs its file."""
    setting = str(setting or '').strip(ASCII_SPACE)
    if not setting or setting.lower() == 'auto':
        setting = system() if callable(system) else system
    return negotiate([setting], interface_languages(folder))


def interface_languages(folder=None):
    """Every language the interface can show, {tag: {"name", "dir"}}: the two in the code and every language file."""
    return {**available_languages(LOCALES if folder is None else folder), **BUILT_IN}


def available_languages(folder=LOCALES, drafts=False):
    """{tag: {"name": its own name, "dir": "ltr" or "rtl"}} for every language file in the folder that is ready to show
    (_meta.ready is true once its screens are signed); drafts=True lists the others too. A file without a _meta name
    (the collector's list of calls, a broken file) is not a language. A draft is still read when a page asks for it by
    name (?freevideo_lang=ru), so it can be checked before it is offered."""
    result = {}
    for path in sorted(Path(folder).glob('*.json')):
        try:
            meta = json.loads(path.read_text(encoding='utf-8')).get('_meta')
        except (OSError, ValueError, AttributeError, RecursionError):
            continue
        if isinstance(meta, dict) and isinstance(meta.get('name'), str) and meta['name'].strip() and (drafts or meta.get('ready') is True):
            result[path.stem] = {'name': meta['name'], 'dir': 'rtl' if meta.get('dir') == 'rtl' else 'ltr'}
    return result
