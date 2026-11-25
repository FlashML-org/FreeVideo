"""Interface languages on the Python side: which language files there are and which one a wanted language gets.

The rule is the one in web/i18n.js (negotiate); tests/i18n_negotiate_cases.json holds the cases both must pass.
A language file is web/locales/<tag>.json with a "_meta" entry ({"name": "繁體中文", "dir": "ltr"}); adding a language
is adding such a file. Nothing here reads settings or changes what is shown yet.
"""
import json
import re
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


# Words read back from text: what a person types ("Cut:", "@Image2", "Subject 1") and what the page wrote in its own
# language. A language file names its own in _meta.keywords, e.g. {"cut": ["Corte"], "subject": ["Sujeto"],
# "image": ["Imagen"]}; the built-in words (English and Chinese, Simplified and Traditional) always work. This is the
# rule of web/i18n.js (keywordsIn, keywordWords); tests/i18n_keywords_cases.json holds the cases both must pass.
KEYWORD_KINDS = ('cut', 'subject', 'image')
BUILT_IN_KEYWORDS = {'cut': ('切', 'cut', 'CUT', 'Cut'), 'subject': ('主体', '主體', 'Subject'), 'image': ('图', '圖', 'Image')}
MAX_KEYWORDS = 8
MAX_KEYWORD_LENGTH = 24
# \s of ECMAScript, so this side and the page agree on what a space is.
_SPACE = '\t\n\v\f\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
_BAD_KEYWORD = re.compile('[:：@<>0-9\r\n\t]|^[' + _SPACE + ']|[' + _SPACE + ']$|[' + _SPACE + ']{2,}')


def keyword_list(words):
    """The valid words of a list, once each, at most MAX_KEYWORDS (a word holds no colon, digit, mark of a mention or
    tag or line break and is at most 24 characters)."""
    if not isinstance(words, (list, tuple)):
        return []
    good = []
    for word in words:
        if isinstance(word, str) and 1 <= len(word) <= MAX_KEYWORD_LENGTH and not _BAD_KEYWORD.search(word) and word not in good:
            good.append(word)
    return good[:MAX_KEYWORDS]


def keywords_in(meta):
    """{kind: [words]} a language file's _meta names; a kind it does not name has none."""
    given = meta.get('keywords') if isinstance(meta, dict) else None
    given = given if isinstance(given, dict) else {}
    return {kind: keyword_list(given.get(kind)) for kind in KEYWORD_KINDS}


def _spellings(word):
    return [word, word.lower(), word.upper(), word[:1].upper() + word[1:]]


def keyword_words(kind, own=()):
    """Every word of a kind that is read: the built-in ones, then the language's own in capitals, lower case and with a
    capital first letter, longest first."""
    words = list(BUILT_IN_KEYWORDS[kind])
    for word in keyword_list(own):
        words.extend(_spellings(word))
    return sorted(dict.fromkeys(words), key=lambda w: (-len(w), w))


def language_keywords(tag, folder=LOCALES):
    """The keywords of a language file (any file, ready or not, as a page can ask for a draft by name); no words for a
    language without a file or without keywords. `tag` is negotiated like a wanted language (pt-PT reads pt-BR's file)."""
    available = available_languages(folder, drafts=True)
    chosen = negotiate([tag], available, fallback=None) if tag else None
    if not chosen:
        return {kind: [] for kind in KEYWORD_KINDS}
    try:
        meta = json.loads((Path(folder) / (chosen + '.json')).read_text(encoding='utf-8')).get('_meta')
    except (OSError, ValueError, AttributeError, RecursionError):
        meta = None
    return keywords_in(meta)


def all_keywords(kind, folder=LOCALES):
    """The words of a kind in every language file: for a server that does not know which language wrote a text."""
    words = []
    for tag in available_languages(folder, drafts=True):
        for word in language_keywords(tag, folder)[kind]:
            if word not in words:
                words.append(word)
    return words[:MAX_KEYWORDS * 64]
