"""Small, offline release descriptions shared by the launcher and workspace.

The product version is editorial; the existing build timestamp/revision still
decides whether an update is newer. Notes never authorize an update.
"""
import argparse
import json
from pathlib import Path
import re

PRODUCT_VERSION = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)')
MAX_NOTES_BYTES = 24000


def product_version(value):
    return value if isinstance(value, str) and len(value) <= 32 and PRODUCT_VERSION.fullmatch(value) else None


def _text(value, limit):
    return (isinstance(value, str) and 0 < len(value.strip()) <= limit
            and not any(ord(c) < 32 for c in value))


def notes(value):
    """Ignore malformed optional metadata so legacy/update checks still work."""
    if not isinstance(value, dict) or value.get('schema') != 1:
        return None
    result = dict(schema=1)
    for language in ('en', 'zh'):
        row = value.get(language)
        if not isinstance(row, dict) or not _text(row.get('summary'), 400):
            return None
        changes = row.get('changes')
        if (not isinstance(changes, list) or not 1 <= len(changes) <= 12
                or not all(_text(item, 600) for item in changes)):
            return None
        result[language] = dict(summary=row['summary'].strip(), changes=[item.strip() for item in changes])
    return result if len(json.dumps(result).encode('utf-8')) <= MAX_NOTES_BYTES else None


MAX_HISTORY = 12


def version_key(value):
    return tuple(int(part) for part in value.split('.'))


def history(value, before=None):
    """Earlier versions' notes, newest first; a malformed entry is left out.

    An update can skip versions; their notes travel with the newest build so
    the dialog can show everything since the installed version.
    """
    rows = []
    for row in value[:MAX_HISTORY] if isinstance(value, list) else []:
        version = product_version(row.get('product_version')) if isinstance(row, dict) else None
        # release_notes.json lists en/zh beside the version; manifests nest them.
        release = (notes(row.get('release_notes') if 'release_notes' in row else dict(row, schema=1))
                   if version else None)
        if release and (before is None or version_key(version) < version_key(before)):
            rows.append(dict(product_version=version, release_notes=release))
    return sorted(rows, key=lambda r: version_key(r['product_version']), reverse=True)


def since(release, installed):
    """The notes of every version after the installed one, up to and excluding `release` itself."""
    current = product_version(installed) if installed else None
    rows = (release or {}).get('release_history') or []
    if not current:
        return []
    return [row for row in rows if version_key(row['product_version']) > version_key(current)]


def optional_fields(value):
    result = {}
    if not isinstance(value, dict):
        return result
    version, release = product_version(value.get('product_version')), notes(value.get('release_notes'))
    if version:
        result['product_version'] = version
    if release:
        result['release_notes'] = release
    earlier = history(value.get('release_history'), before=version)
    if version and earlier:
        result['release_history'] = earlier
    return result


def catalog(package=None):
    """Builds must fail on incomplete translations, before uploading anything."""
    package = Path(package) if package is not None else Path(__file__).parent
    value = json.loads((package / 'release_notes.json').read_text(encoding='utf-8'))
    version, release = product_version(value.get('product_version')), notes(value)
    if not version or not release:
        raise ValueError('Release notes need a product version and complete English/Chinese text')
    earlier = history(value.get('history'), before=version)
    if len(earlier) != len(value.get('history') or []):
        raise ValueError('Every earlier version in release_notes.json needs complete English/Chinese text')
    return dict(product_version=version, release_notes=release, **({'release_history': earlier} if earlier else {}))


def public_details(value):
    """Allowlisted display fields, without download credentials or URLs."""
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in ('version', 'revision', 'built_at') if key in value}
    result.update(optional_fields(value))
    return result


def installed_details(package, fallback=None):
    package = Path(package)
    try:
        from .launcher_update import build_identity
        return public_details(build_identity(json.loads((package / 'build-identity.json').read_text(encoding='utf-8'))))
    except (OSError, ValueError, TypeError):
        # Do not attach today's notes to an old stamped build.
        try:
            return dict(version=(package / 'build-version.txt').read_text(encoding='utf-8').strip())
        except OSError:
            result = dict(version=fallback, development=True)
            try:
                result.update(catalog(package))
            except (OSError, ValueError, TypeError):
                pass
            return result


def markdown(value):
    """Render both languages from the exact metadata shipped in this artifact."""
    data = optional_fields(value)
    release = data.get('release_notes')
    if not release or not data.get('product_version'):
        raise ValueError('Build has no bilingual release notes')
    def escape(text):
        return re.sub(r'([\\`*_{}\[\]()#+.!<>|~-])', r'\\\1', text)
    title = 'FreeVideo v' + data['product_version']
    rows = ['# ' + title, '', 'Build / 构建号: ' + escape(str(value['version'])), '']
    for language, heading in (('en', "What's new"), ('zh', '更新内容')):
        row = release[language]
        rows.extend(['## ' + heading, '', escape(row['summary']), ''])
        rows.extend('- ' + escape(item) for item in row['changes'])
        rows.append('')
    return '\n'.join(rows) + '\n'


def main():
    parser = argparse.ArgumentParser(description='Render the release notes stamped into a build')
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--prepend', action='store_true', help='Keep existing installation instructions after the notes')
    args = parser.parse_args()
    rendered = markdown(json.loads(args.build.read_text(encoding='utf-8')))
    if args.prepend:
        rendered += args.out.read_text(encoding='utf-8')
    args.out.write_text(rendered, encoding='utf-8')


if __name__ == '__main__':
    main()
