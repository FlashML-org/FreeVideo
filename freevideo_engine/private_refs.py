"""Which of FreeVideo's own input files the records on this machine still use.

Read-only: it lists (kind, key) tokens as ``private_store.classify`` gives them, from every record
that can name an input file: kept and recently deleted works, Agent jobs, cards and productions,
editor projects, conversations, ComfyUI's saved workflows and its queue. A record that cannot be read
makes the scan fail rather than answer less, so nothing it would have named is taken as unused.
"""
import json
import logging
import os
from pathlib import Path
import re
import stat
import time

from .private_store import HEX32, UUID, REPARSE_POINT

# Paths as records write them: relative or absolute, with forward or (escaped) back slashes.
SEP = r'(?:/|\\{1,2})'
TOKENS = (
    ('upload', re.compile(r'freevideo' + SEP + '(' + HEX32 + ')(?![0-9a-f])')),
    ('page', re.compile(r'freevideo-image-ref' + SEP + '(' + UUID + r')\.(?:png|jpg|webp)')),
    ('page', re.compile(r'freevideo-image-(?:reference|mask)-(' + UUID + r')\.png')),
    ('draft', re.compile(r'freevideo-draft' + SEP + '(' + UUID + r')\.(?:jpg|png)')),
    ('mask', re.compile(r'freevideo-image' + SEP + '(' + HEX32 + r')\.png')),
    # Conversations and jobs name an attachment by its bare file name.
    ('asset', re.compile(r'(?<![0-9a-f])([0-9a-f]{64})\.(?:png|jpg|jpeg|webp)')),
    ('agent', re.compile(r'freevideo' + SEP + 'agent' + SEP + '([A-Za-z0-9_-]{1,64})')),
)
RECORD_LIMIT = 64 * 1024 * 1024
# How often tidying the draft starts again when the page saved it in between.
DRAFT_TRIES = 5


class ScanIncomplete(OSError):
    """A record could not be read; callers keep everything rather than act on a partial answer."""


def tokens(text):
    found = set()
    for kind, pattern in TOKENS:
        found.update((kind, key) for key in pattern.findall(text))
    return found


def _plain_dir(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return (stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and not getattr(info, 'st_file_attributes', 0) & REPARSE_POINT)


def _read_text(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return ''
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        # A link is never followed, but whatever it names may be a record we cannot see: be safe.
        raise ScanIncomplete('Unreadable record %s' % Path(path).name)
    if info.st_size > RECORD_LIMIT:
        raise ScanIncomplete('Oversized record %s' % Path(path).name)
    try:
        with open(path, 'rb') as stream:
            return stream.read().decode('utf-8', 'replace')
    except FileNotFoundError:
        return ''
    except OSError as error:
        raise ScanIncomplete(str(error)) from error


def _walk(folder, suffixes, *, skip=()):
    """Files under ``folder`` with one of ``suffixes``, never through a link or junction."""
    if not _plain_dir(folder):
        return
    for parent, folders, files in os.walk(folder):
        folders[:] = [name for name in folders if name not in skip and _plain_dir(os.path.join(parent, name))]
        for leaf in files:
            if leaf.endswith(suffixes):
                yield os.path.join(parent, leaf)


def _is_draft(path):
    """The creation draft the page keeps in the user folder. It names the pictures last set up, only so a start
    without the browser's copy can offer them again: it never keeps a file alive, and what is gone leaves it."""
    return Path(path).as_posix().endswith('/freevideo/prompt-draft.json')


def scrub_draft(user_root=None, input_root=None):
    """Takes the reference pictures that no longer exist out of the saved creation draft, so nothing of a deleted
    file stays in it. Never raises; returns how many rows went.

    The page saves the draft through ComfyUI's own file route, so no lock here is shared with it. The file is read,
    changed and written back only while its bytes are still the ones that were read; a save that lands in between
    makes this start again from the new file, so the user's newer draft is never replaced by an older one."""
    removed = 0
    try:
        user_root = user_root if user_root is not None else user_directory()
        input_root = input_root if input_root is not None else input_directory()
        if user_root is None or input_root is None or not _plain_dir(user_root):
            return 0
        base = os.path.realpath(input_root)

        def there(row):
            name = re.sub(r' \[(?:input|output|temp)\]$', '', row.get('file', '')) if isinstance(row, dict) else ''
            target = os.path.realpath(os.path.join(base, *re.split(r'[\\/]', name))) if name else base
            return target != base and target.startswith(base + os.sep) and os.path.exists(target)

        for path in _walk(Path(user_root), ('.json',)):
            if not _is_draft(path):
                continue
            for attempt in range(DRAFT_TRIES):
                with open(path, 'rb') as stream:
                    raw = stream.read()
                draft = json.loads(raw.decode('utf-8'))
                rows = draft.get('assets') if isinstance(draft, dict) else None
                if not isinstance(rows, list):
                    break
                kept = [row for row in rows if there(row)]
                if len(kept) == len(rows):
                    break
                draft['assets'] = kept
                temporary = path + '.tmp'
                with open(temporary, 'w', encoding='utf-8') as stream:
                    json.dump(draft, stream, ensure_ascii=False)
                with open(path, 'rb') as stream:
                    unchanged = stream.read() == raw
                if unchanged:
                    os.replace(temporary, path)
                    removed += len(rows) - len(kept)
                    break
                os.unlink(temporary)
            else:
                logging.warning('[FreeVideo] The saved creation draft kept changing; it was left as it is.')
    except Exception:
        logging.warning('[FreeVideo] Could not tidy the saved creation draft.', exc_info=True)
    return removed


def scan(*, output_root=None, user_root=None, queue=None, extra=(), roots=()):
    """Every token a record still names. Raises ScanIncomplete when a record exists but cannot be read.

    ``roots`` are more folders of records (an Agent jobs folder kept elsewhere); a card tombstone there holds nothing."""
    found = set(extra)
    for folder in roots:
        for path in _walk(Path(folder), ('.json', '.jsonl')):
            if '/cast/.deleted/' not in Path(path).as_posix():
                found |= tokens(_read_text(path))
    if output_root is not None:
        root = Path(output_root) / 'FreeVideo'
        # Kept works, the trash a deletion can still be undone from, Agent jobs, cards, productions,
        # sessions and editor projects. A card's tombstone is not a holder.
        for path in _walk(root, ('.json', '.jsonl'), skip=('.private',)):
            relative = Path(path).relative_to(root).as_posix()
            if relative.startswith('.agent/jobs/cast/.deleted/'):
                continue
            found |= tokens(_read_text(path))
    if user_root is not None:
        # ComfyUI's saved workflows and FreeVideo's own page records in the user folder.
        for path in _walk(Path(user_root), ('.json',)):
            if not _is_draft(path):
                found |= tokens(_read_text(path))
    if queue is not None:
        try:
            running, pending = queue.get_current_queue_volatile()
        except AttributeError:
            running, pending = queue.get_current_queue()
        for row in list(running) + list(pending):
            found |= tokens(json.dumps(row, default=str))
    return found


def released(output_root):
    """Tokens of files that items the user deleted named: cards' tombstones, older ones holding the whole card
    and the list kept when they were reduced. These need no grace once nothing else uses them."""
    found = set()
    if output_root is None:
        return found
    folder = Path(output_root) / 'FreeVideo' / '.agent' / 'jobs' / 'cast' / '.deleted'
    for path in _walk(folder, ('.json',)):
        try:
            text = _read_text(path)
        except ScanIncomplete:
            continue
        if Path(path).name == 'released.json':
            try:
                value = json.loads(text)
            except ValueError:
                continue
            found.update(tuple(key.split(':', 1)) for key in value if isinstance(key, str) and ':' in key)
        else:
            found |= tokens(text)
    return found


def run_sweep(*, input_root, output_root=None, user_root=None, queue=None, extra=(), root=None, now=None):
    """One scan of earlier files; nothing is removed when any record cannot be read."""
    from . import private_store
    try:
        referenced = scan(output_root=output_root, user_root=user_root, queue=queue, extra=extra)
    except OSError as error:
        logging.warning('[FreeVideo] Private file cleanup skipped: a record could not be read (%s).',
                        type(error).__name__)
        return None
    summary = private_store.sweep(input_root, referenced, root=root, now=now, released=released(output_root))
    if not summary.get('dry_run'):
        scrub_draft(user_root, input_root)
    if output_root is not None:
        # Folders of requests that ended without a video: listed when cleanup only lists, removed otherwise.
        from .comfy_library import failed_runs
        runs = failed_runs(output_root, now=now)
        if runs:
            # The folder of a request that ended without a video holds its prompt and upload copies; it goes with the
            # rest once the days a failure report can be sent are over.
            from .comfy_library import remove_failed_run
            for identity, size in runs:
                status = 'would-remove'
                if not summary['dry_run']:
                    try:
                        status = 'removed' if remove_failed_run(output_root, identity) else 'kept'
                    except Exception:
                        logging.warning('[FreeVideo] Could not remove a request folder.', exc_info=True)
                        status = 'kept'
                summary['items'].append(dict(path='FreeVideo/' + identity, kind='run', bytes=size, status=status))
                if status in ('removed', 'would-remove'):
                    summary['files'] += 1
                    summary['bytes'] += size
            try:
                private_store._write(private_store.store_root(root) / 'sweep-log.json', summary)
            except OSError:
                pass
    return summary


SWEEP_DELAY = 120.
SWEEP_EVERY = 24 * 3600.


def start_sweeps(context, *, delay=SWEEP_DELAY, every=SWEEP_EVERY):
    """At startup, after ``delay`` seconds, and then once a day. ``context()`` returns run_sweep's arguments."""
    import threading

    def loop():
        wait = delay
        while True:
            time.sleep(wait)
            wait = every
            try:
                # Cached encodings that keep a prompt as text are rebuilt when needed, so they go now.
                from . import private_store
                from .paths import data_root
                removed = private_store.purge_prompt_text(data_root() / 'input-cache', data_root() / 'tuning')
                if removed:
                    logging.info('[FreeVideo] Removed %d cached encodings that kept a prompt as text.', removed)
            except Exception:
                logging.warning('[FreeVideo] Could not check cached encodings.', exc_info=True)
            try:
                from . import private_store
                left = private_store.clean_temp()
                if left:
                    logging.info('[FreeVideo] Removed %d working folders a stopped picture request left.', left)
            except Exception:
                logging.warning('[FreeVideo] Could not remove left working folders.', exc_info=True)
            try:
                run_sweep(**context())
            except Exception:
                logging.warning('[FreeVideo] Private file cleanup failed.', exc_info=True)
    thread = threading.Thread(target=loop, name='freevideo-private-cleanup', daemon=True)
    thread.start()
    return thread


# The Settings page: the list a scan would clean, cleaned when the user confirms it, and pictures that earlier
# versions put in the input folder itself under the user's own names, each removed only when the user says so.

def _held(context, root=None):
    from . import private_store
    held = scan(output_root=context.get('output_root'), user_root=context.get('user_root'),
                queue=context.get('queue'), extra=context.get('extra', ()), roots=context.get('roots', ()))
    held |= private_store.pinned(root=root)
    for record in private_store.owners(root).values():
        held.update(private_store.classify(r) for r in record['refs'])
    return held


def review(context, *, root=None, now=None):
    """What a cleanup would remove now, as rows the page can show and send back. Removes nothing."""
    from . import private_store
    now = time.time() if now is None else now
    held = _held(context, root)
    gone = released(context.get('output_root'))
    rows = []
    for row in private_store.candidates(context['input_root'], now=now):
        token = (row['kind'], row['key'])
        if token in held or (token not in gone and row['age'] < private_store.grace(row['kind'])):
            continue
        rows.append(dict(token='%s:%s' % token, kind=row['kind'], bytes=row['bytes'],
                         path=private_store.redacted(row['path'], root=root)))
    # Folders of requests that ended without a video, past the time their failure report is offered.
    if context.get('output_root') is not None:
        from .comfy_library import failed_runs
        for identity, size in failed_runs(context['output_root'], now=now):
            rows.append(dict(token='run:' + identity, kind='run', bytes=size, path='FreeVideo/' + identity))
    return dict(items=rows, files=len(rows), bytes=sum(row['bytes'] for row in rows))


def clean(context, tokens, *, root=None, now=None):
    """Remove the listed files the user confirmed, each checked again first: still FreeVideo's own name, still
    used by nothing, still past its grace (or its owner deleted). Anything else on the list stays."""
    from . import private_store
    if not isinstance(tokens, list) or len(tokens) > 20000 or not all(isinstance(t, str) for t in tokens):
        raise ValueError('Invalid list')
    wanted = {tuple(token.split(':', 1)) for token in tokens if ':' in token}
    now = time.time() if now is None else now
    held = _held(context, root)
    gone = released(context.get('output_root'))
    removed, freed = 0, 0
    with private_store.LOCK:
        for row in private_store.candidates(context['input_root'], now=now):
            token = (row['kind'], row['key'])
            if token not in wanted or token in held:
                continue
            if token not in gone and row['age'] < private_store.grace(row['kind']):
                continue
            if row['kind'] == 'upload':
                status, size, _ = private_store.remove_upload(context['input_root'], row['key'])
            else:
                status, size = private_store.safe_remove(context['input_root'], row['path'],
                                                         folders=4 if row['kind'] == 'agent' else 1)
            if status == 'removed':
                removed += 1
                freed += size
    if context.get('output_root') is not None:
        from .comfy_library import remove_failed_run
        for kind, identity in sorted(wanted):
            if kind == 'run':
                size = remove_failed_run(context['output_root'], identity, now=now)
                removed += size > 0
                freed += size
    if removed:
        scrub_draft(context.get('user_root'), context['input_root'])
    logging.info('[FreeVideo] Private file cleanup confirmed by the user: %d files, %.1f MB.', removed, freed / 1e6)
    return dict(removed=removed, freed_bytes=freed)


LEGACY_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')
# ComfyUI's own sample picture.
LEGACY_SKIP = ('example.png',)


def legacy(input_root):
    """Pictures in the input folder itself that are not FreeVideo's own names: earlier image pages put reference
    pictures there under the user's file names, and they cannot be told apart from the user's own files."""
    from . import private_store
    rows = []
    try:
        root = Path(input_root)
        entries = sorted(os.scandir(root), key=lambda entry: entry.name)
    except OSError:
        return rows
    for entry in entries:
        name = entry.name
        if (not name.lower().endswith(LEGACY_SUFFIXES) or name in LEGACY_SKIP or private_store.classify(name)
                or name.startswith('.')):
            continue
        try:
            info = os.lstat(entry.path)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & REPARSE_POINT and info.st_nlink == 1:
            rows.append(dict(name=name, bytes=info.st_size, modified=info.st_mtime))
    return rows


def legacy_delete(context, name):
    """Remove one picture from the input folder itself that the user confirmed in Settings; refused when any
    record still names it, or it is a link, a hard link, read-only, on another volume or not a picture."""
    from . import private_store
    if (not isinstance(name, str) or not name or '/' in name or '\\' in name or ':' in name or name in ('.', '..')
            or name not in {row['name'] for row in legacy(context['input_root'])}):
        raise ValueError('Not one of the listed pictures')
    for text in _record_texts(context):
        if name in text:
            return dict(removed=False, reason='in-use')
    status, size = private_store.safe_remove(context['input_root'], name, patterns=[re.compile(re.escape(name))], folders=0)
    return dict(removed=status == 'removed', reason=None if status == 'removed' else status, freed_bytes=size)


def _record_texts(context):
    output_root, user_root = context.get('output_root'), context.get('user_root')
    if output_root is not None:
        root = Path(output_root) / 'FreeVideo'
        for path in _walk(root, ('.json', '.jsonl'), skip=('.private',)):
            if not Path(path).relative_to(root).as_posix().startswith('.agent/jobs/cast/.deleted/'):
                yield _read_text(path)
    if user_root is not None:
        for path in _walk(Path(user_root), ('.json',)):
            if not _is_draft(path):
                yield _read_text(path)
    queue = context.get('queue')
    if queue is not None:
        try:
            running, pending = queue.get_current_queue_volatile()
        except AttributeError:
            running, pending = queue.get_current_queue()
        for row in list(running) + list(pending):
            yield json.dumps(row, default=str)


def register_routes(server, web, folder_paths, context):
    """The Settings page's cleanup: GET the list, POST the confirmed list; the earlier pictures one by one."""
    import asyncio
    from . import private_store
    headers = {'Cache-Control': 'no-store'}

    async def view_revalidates(request, handler):
        # A picture or video ComfyUI serves is checked again on every use, so the browser keeps no copy of a file
        # that was deleted; an unchanged file still answers 304.
        response = await handler(request)
        if request.path in ('/view', '/api/view') and 'Cache-Control' not in response.headers:
            response.headers['Cache-Control'] = 'private, no-cache'
        return response
    app = getattr(server, 'app', None)
    if getattr(web, 'middleware', None) is not None and app is not None and not getattr(server, '_freevideo_view_revalidates', False):
        view_revalidates = web.middleware(view_revalidates)
        try:
            app.middlewares.append(view_revalidates)
            server._freevideo_view_revalidates = True
        except (AttributeError, RuntimeError):
            pass

    @server.routes.get('/freevideo/private/review')
    async def private_review(request):
        try:
            result = await asyncio.to_thread(review, context())
        except OSError:
            return web.json_response({'error': 'unavailable'}, status=503, headers=headers)
        return web.json_response(dict(result, enabled=private_store.deletion_enabled()), headers=headers)

    @server.routes.post('/freevideo/private/clean')
    async def private_clean(request):
        try:
            value = await request.json()
            result = await asyncio.to_thread(clean, context(), value.get('tokens') if isinstance(value, dict) else None)
        except (OSError, ValueError, TypeError) as error:
            return web.json_response({'error': str(error)}, status=400, headers=headers)
        return web.json_response(result, headers=headers)

    @server.routes.get('/freevideo/private/legacy')
    async def private_legacy(request):
        return web.json_response(dict(items=await asyncio.to_thread(legacy, context()['input_root'])), headers=headers)

    @server.routes.post('/freevideo/private/forgotten')
    async def private_forgotten(request):
        # The page's own prompt history: which of its prompts belonged only to works deleted for good.
        try:
            value = await request.json()
            prompts = value.get('prompts') if isinstance(value, dict) else None
            if not isinstance(prompts, list) or len(prompts) > 1000:
                raise ValueError('Invalid list')
            result = await asyncio.to_thread(private_store.forgotten, prompts)
        except (OSError, ValueError, TypeError) as error:
            return web.json_response({'error': str(error)}, status=400, headers=headers)
        return web.json_response({'forget': result}, headers=headers)

    def cache_roots():
        from .paths import data_root
        return [data_root() / 'input-cache', data_root() / 'tuning' / 'conditioning']

    @server.routes.get('/freevideo/private/cache')
    async def private_cache(request):
        from .input_cache import limit_bytes
        from .paths import data_root
        usage = await asyncio.to_thread(private_store.cache_usage, cache_roots())
        return web.json_response(dict(usage, limit=limit_bytes(data_root() / 'input-cache'), busy=generating()), headers=headers)

    @server.routes.post('/freevideo/private/cache-limit')
    async def private_cache_limit(request):
        try:
            value = await request.json()
            chosen = value.get('bytes') if isinstance(value, dict) else None
            if type(chosen) is not int or not 256 * 2**20 <= chosen <= 2**40:
                raise ValueError('Invalid size')
            await asyncio.to_thread(private_store.set_setting, 'cache_bytes', chosen)
        except (OSError, ValueError, TypeError) as error:
            return web.json_response({'error': str(error)}, status=400, headers=headers)
        return web.json_response({'limit': chosen}, headers=headers)

    def generating():
        queue = context().get('queue')
        try:
            running, pending = queue.get_current_queue() if queue is not None else ([], [])
        except Exception:
            running, pending = [], []
        image = getattr(server, '_freevideo_image_gen', None)
        try:
            image_busy = bool(image is not None and (image.busy() or bool(image.pending)))
        except Exception:
            image_busy = False
        return bool(running or pending) or image_busy

    @server.routes.post('/freevideo/private/cache-clear')
    async def private_cache_clear(request):
        # Not while something is being made: a running request may be reading or writing these files.
        if generating():
            return web.json_response({'error': 'busy'}, status=409, headers=headers)
        freed = await asyncio.to_thread(private_store.clear_cache, cache_roots())
        return web.json_response({'freed_bytes': freed}, headers=headers)

    @server.routes.post('/freevideo/private/legacy-delete')
    async def private_legacy_delete(request):
        try:
            value = await request.json()
            result = await asyncio.to_thread(legacy_delete, context(), value.get('name') if isinstance(value, dict) else None)
        except (OSError, ValueError, TypeError) as error:
            return web.json_response({'error': str(error)}, status=400, headers=headers)
        return web.json_response(result, headers=headers)


def release_attachments(context, names):
    """After a conversation is deleted: remove the chat pictures it named, with their name records, when no other
    conversation, job, card or open page names them. Returns how many went."""
    from . import private_store
    wanted = {name for name in names if isinstance(name, str) and re.fullmatch(r'[0-9a-f]{64}\.(?:png|jpg|jpeg|webp)', name)}
    if not wanted:
        return 0
    try:
        held = _held(context)
    except OSError:
        logging.warning('[FreeVideo] Kept the pictures of a deleted conversation: a record could not be read.')
        return 0
    removed = 0
    for name in sorted(wanted):
        if ('asset', name[:64]) in held:
            continue
        for relative in ('freevideo/agent/assets/' + name, 'freevideo/agent/assets/.' + name + '.json'):
            status, _ = private_store.safe_remove(context['input_root'], relative, folders=0)
            removed += status == 'removed'
    return removed


def user_directory():
    """ComfyUI's user folder (saved workflows), or None outside ComfyUI."""
    try:
        import folder_paths
        return folder_paths.get_user_directory()
    except (ImportError, AttributeError):
        return None


def input_directory():
    """ComfyUI's input folder, or None outside ComfyUI."""
    try:
        import folder_paths
        return folder_paths.get_input_directory()
    except (ImportError, AttributeError):
        return None


def refs_in(values, input_root):
    """The input-relative names, in FreeVideo's own naming, of the files under ``input_root`` that ``values`` (paths,
    or records holding paths) point at. Anything else is left out."""
    from . import private_store
    if input_root is None:
        return []
    try:
        base = Path(os.path.realpath(input_root))
    except OSError:
        return []
    found = []

    def walk(value):
        if isinstance(value, str):
            try:
                relative = Path(os.path.realpath(value)).relative_to(base).as_posix() if os.path.isabs(value) else value
            except (ValueError, OSError):
                return
            if private_store.classify(relative):
                found.append(relative)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
    walk(values)
    return list(dict.fromkeys(found))


def hold(owner_id, values=(), *, receipts=(), input_root=None):
    """Record that ``owner_id`` (video:<run>, image:<file>) uses the FreeVideo input files ``values`` point at and the
    input-cache ``receipts`` its run wrote or read, so deleting it can remove them. Never raises."""
    from . import private_store
    try:
        root = input_root if input_root is not None else input_directory()
        refs = refs_in(values, root)
        derived = [row for row in receipts if private_store._derived_ok(row)]
        if refs or derived:
            private_store.hold(owner_id, refs=refs, derived=derived)
    except Exception:
        logging.warning('[FreeVideo] Could not note the files a request uses.', exc_info=True)


def release_owner(owner_id, context=None, *, input_root=None):
    """The user deleted what ``owner_id`` made: remove the input files and cached encodings only it used. A file any
    record, queued job or open page still names stays. When a record cannot be read nothing is released."""
    from . import private_store
    from .paths import data_root
    try:
        held = private_store.owner(owner_id)
        if not held['refs'] and not held['derived']:
            return None
        context = dict(context if context is not None else default_context())
        if input_root is not None:
            context['input_root'] = input_root
        try:
            # Records and pages only: release itself leaves what another owner holds, and this owner is the one going.
            keep = scan(output_root=context.get('output_root'), user_root=context.get('user_root'),
                        queue=context.get('queue'), extra=context.get('extra', ()), roots=context.get('roots', ()))
            keep |= private_store.pinned()
        except OSError:
            logging.warning('[FreeVideo] Kept the files of a deleted work: a record could not be read.')
            return None
        released = private_store.release(owner_id, input_root=context['input_root'], cache_root=data_root() / 'input-cache',
                                         keep=keep)
        scrub_draft(context.get('user_root'), context['input_root'])
        return released
    except Exception:
        logging.warning('[FreeVideo] Could not release what a deleted work held.', exc_info=True)
        return None


def default_context():
    """run_sweep's arguments for the running ComfyUI."""
    import folder_paths
    try:
        from server import PromptServer
        server = PromptServer.instance
    except (ImportError, AttributeError):
        server = None
    image = getattr(server, '_freevideo_image_gen', None)
    return dict(input_root=folder_paths.get_input_directory(), output_root=folder_paths.get_output_directory(),
                user_root=folder_paths.get_user_directory(), queue=getattr(server, 'prompt_queue', None),
                extra=image.held_tokens() if image is not None and hasattr(image, 'held_tokens') else ())


def release_tokens(context, tokens):
    """Remove at once the FreeVideo files of an item the user deleted (a card) that nothing else uses: no record,
    queued job or open page names them. Anything still used stays for the cleanup to look at again. Returns how many
    files went."""
    from . import private_store
    wanted = {tuple(token.split(':', 1)) for token in tokens if isinstance(token, str) and ':' in token}
    if not wanted:
        return 0
    try:
        held = _held(context)
    except OSError:
        logging.warning('[FreeVideo] Kept the files of a deleted card: a record could not be read.')
        return 0
    removed = 0
    with private_store.LOCK:
        for row in private_store.candidates(context['input_root']):
            token = (row['kind'], row['key'])
            if token not in wanted or token in held:
                continue
            if row['kind'] == 'upload':
                status = private_store.remove_upload(context['input_root'], row['key'])[0]
            else:
                status = private_store.safe_remove(context['input_root'], row['path'],
                                                   folders=4 if row['kind'] == 'agent' else 1)[0]
            removed += status == 'removed'
    if removed:
        scrub_draft(context.get('user_root'), context['input_root'])
    return removed
