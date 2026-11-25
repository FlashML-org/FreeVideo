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


# The language list (the launcher's language key, the page's settings): languages in this order first, the rest by tag.
# Each is shown by its own name; the English name is for search only.
LANGUAGE_ORDER = ('zh-Hans', 'zh-Hant', 'en', 'es', 'pt-BR', 'ru', 'uk', 'ja', 'ko', 'fr', 'de', 'tr', 'id', 'vi', 'it', 'ar', 'fa')
ENGLISH_NAMES = {'zh-Hans': 'Chinese Simplified', 'zh-Hant': 'Chinese Traditional', 'en': 'English', 'es': 'Spanish',
                 'pt-BR': 'Portuguese Brazil', 'ru': 'Russian', 'uk': 'Ukrainian', 'ja': 'Japanese', 'ko': 'Korean',
                 'fr': 'French', 'de': 'German', 'tr': 'Turkish', 'id': 'Indonesian', 'vi': 'Vietnamese', 'it': 'Italian',
                 'ar': 'Arabic', 'fa': 'Persian'}


def language_choices(folder=None):
    """[{"tag", "name" (its own), "english", "dir"}] for every language the interface can show, in list order."""
    languages = interface_languages(folder)
    rank = {tag: index for index, tag in enumerate(LANGUAGE_ORDER)}
    return [dict(tag=tag, name=meta['name'], english=ENGLISH_NAMES.get(tag, tag), dir=meta.get('dir', 'ltr'))
            for tag, meta in sorted(languages.items(), key=lambda item: (rank.get(item[0], len(rank)), item[0]))]

# Looking up text (the launcher and Python side; web/i18n.js lookup is the same rule).
KEY_SEPARATOR = '␟'
CODE_LANGUAGES = ('en', 'zh-Hans')   # their text is the en and zh written in the code, never read from a file
_catalogs = {}


def fill(text, values):
    """{name} placeholders filled from values; a name values does not hold stays as written."""
    import re
    return re.sub(r'\{(\w+)\}', lambda match: str(values[match.group(1)]) if match.group(1) in values else match.group(0),
                  str(text))


PLURAL_FORMS = ('zero', 'one', 'two', 'few', 'many', 'other')
# Languages whose nouns do not change with the number: every number reads as 'other'.
_PLURAL_OTHER_ONLY = {'ja', 'ko', 'zh', 'vi', 'id', 'th', 'ms'}


def plural_form(language, number):
    """The CLDR cardinal plural form of a number in a language, as Intl.PluralRules gives it in the page (one, few, many,
    other), for the languages FreeVideo has files for; tests/i18n_plural_cases.json holds the browser's answers."""
    try:
        n = abs(float(number))
    except (TypeError, ValueError):
        return 'other'
    if n != n or n == float('inf'):
        return 'other'
    whole = n == int(n)
    i = int(n)
    base = str(language or '').replace('_', '-').split('-')[0].lower()
    if base in _PLURAL_OTHER_ONLY:
        return 'other'
    if base in ('ru', 'uk'):
        if not whole:
            return 'other'
        if i % 10 == 1 and i % 100 != 11:
            return 'one'
        if 2 <= i % 10 <= 4 and not 12 <= i % 100 <= 14:
            return 'few'
        return 'many'
    if base in ('fr', 'pt') and str(language).lower() != 'pt-pt':
        return 'one' if i in (0, 1) else 'other'
    return 'one' if whole and i == 1 else 'other'


def plural_text(entry, values, language):
    """An entry written as plural forms ({"one": ..., "few": ..., "many": ..., "other": ...}) as the text for the number in
    values: the placeholder its "_by" names, else n, else count. A form the entry lacks, or no number, gives "other".
    A string is itself; anything else is None. web/i18n.js pluralText."""
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict) or not isinstance(entry.get('other'), str):
        return None
    name = entry.get('_by') if isinstance(entry.get('_by'), str) else next((k for k in ('n', 'count') if values and k in values), None)
    form = plural_form(language, values[name]) if name and values and name in values else 'other'
    return entry[form] if form in PLURAL_FORMS and isinstance(entry.get(form), str) else entry['other']


def lookup(catalog, en, zh, values=None, language=None):
    """A language file's text for a pair: the entry for the English and Chinese together (when the English has more than
    one meaning), then for the English alone, then the English itself. An entry with plural forms gives the one for the
    number in values, by the plural rules of language (the file's own tag when it was read by catalog())."""
    meta_tag = (catalog.get('_meta') or {}).get('tag') if isinstance(catalog, dict) and isinstance(catalog.get('_meta'), dict) else None
    rules = language or meta_tag

    def own(key):
        value = catalog.get(key) if isinstance(catalog, dict) else None
        return plural_text(value, values, rules) if key != '_meta' else None
    text = own(en + KEY_SEPARATOR + zh)
    text = own(en) if text is None else text
    if text is None:
        # A file whose fallback is zh-Hans (Traditional Chinese) shows text it does not hold as the code's Simplified
        # Chinese in its own characters, not as English: the rule of web/i18n.js lookup.
        meta = catalog.get('_meta') if isinstance(catalog, dict) else None
        text = from_simplified(catalog, zh) if isinstance(meta, dict) and meta.get('fallback') == 'zh-Hans' else en
    return fill(text, values) if values else text


_char_tables = {}


def from_simplified(catalog, text):
    """Simplified Chinese text in the characters of a file's _meta.chars (pairs of a Simplified character and the one it
    becomes); the text itself when the file has none. web/i18n.js fromSimplified."""
    meta = catalog.get('_meta') if isinstance(catalog, dict) else None
    chars = meta.get('chars') if isinstance(meta, dict) else None
    if not isinstance(chars, str) or not chars:
        return str(text)
    table = _char_tables.get(chars)
    if table is None:
        table = _char_tables[chars] = dict(zip(chars[0::2], chars[1::2]))
    return ''.join(table.get(ch, ch) for ch in str(text))


def catalog(language, folder=None):
    """The entries of a language file ({} for the code's own languages, a missing file or an unreadable one). Read once."""
    folder = LOCALES if folder is None else folder
    key = (str(folder), language)
    if key not in _catalogs:
        value = {}
        if language and language not in CODE_LANGUAGES and '/' not in language and '\\' not in language:
            try:
                loaded = json.loads((Path(folder) / (language + '.json')).read_text(encoding='utf-8'))
                if isinstance(loaded, dict):
                    # Texts, and entries with plural forms (an object whose "other" is a text).
                    value = {k: v for k, v in loaded.items() if k != '_meta'
                             and (isinstance(v, str) or isinstance(v, dict) and isinstance(v.get('other'), str))}
                    # What a missing entry falls back to (web/i18n.js lookup): Traditional Chinese reads the code's
                    # Simplified Chinese in its own characters.
                    meta = loaded.get('_meta') if isinstance(loaded.get('_meta'), dict) else {}
                    kept = {k: meta[k] for k in ('fallback', 'chars') if isinstance(meta.get(k), str)}
                    # Its plural rules are the file's language.
                    value['_meta'] = dict(kept, tag=language)
            except (OSError, ValueError):
                pass
        _catalogs[key] = value
    return _catalogs[key]


def code_language(language):
    """The code language a tag reads as during the change-over: 'zh' (and any zh-Hans form) is Simplified Chinese."""
    tag = str(language or '').replace('_', '-').lower()
    if tag == 'zh' or tag.startswith('zh-hans') or tag in ('zh-cn', 'zh-sg'):
        return 'zh-Hans'
    return 'en' if tag in ('', 'en') or tag.startswith('en-') else language


def tr(en, zh, language, values=None, folder=None):
    """The text for a pair in a language: the code's own English or Chinese, else the language file's entry (English
    when it has none). {name} placeholders are filled from values in every language."""
    language = code_language(language)
    if language == 'zh-Hans':
        return fill(zh, values) if values else zh
    if language == 'en':
        return fill(en, values) if values else en
    return lookup(catalog(language, folder), en, zh, values, language)
