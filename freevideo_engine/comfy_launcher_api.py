"""Launcher handshake, the bundled default workflow, and the page check result."""
import json
import logging
import os
import time
from pathlib import Path


def page_report(value):
    """The browser page's own check, bounded; None for anything else.

    'loading' arrives as soon as FreeVideo's page script starts, before its
    check ends with 'ready' or 'error'.
    """
    if not isinstance(value, dict) or value.get('state') not in ('loading', 'ready', 'error'):
        return None
    rows = lambda key, count, limit: ([p[:limit] for p in value[key][:count] if isinstance(p, str)]
                                      if isinstance(value.get(key), list) else [])
    text = lambda key, limit: value.get(key)[:limit] if isinstance(value.get(key), str) else ''
    return dict(state=value['state'], problems=rows('problems', 8, 500), errors=rows('errors', 40, 300),
                ready=value.get('ready') is True, launch=value.get('launch') is True, view=text('view', 20),
                agent=text('agent', 300), frontend=text('frontend', 40), page=text('page', 200),
                client=text('client', 80), at=time.time())


PAGE_SCRIPTS_MISSING = (
    'FreeVideo’s page scripts have not run in this browser, so the creative workspace is missing. '
    'Press Ctrl+F5 to reload the page. If it stays missing, open the FreeVideo launcher, '
    'click Settings › Install / repair, and open FreeVideo again. '
    '浏览器没有运行 FreeVideo 的页面脚本，所以看不到创作面板。请按 Ctrl+F5 强制刷新页面；'
    '仍然没有时，请在 FreeVideo 启动器的“设置”里点击“安装 / 修复”，然后重新打开 FreeVideo。')


def page_notice(server):
    """Text for the generating node when no FreeVideo page has reported to this ComfyUI.

    The page check names its problems on the page, but it is itself one of
    FreeVideo's page scripts. When none of them runs, the nodes still generate
    and nothing on the page says the workspace is missing; ComfyUI's own node
    text is then the only place left to say so.
    """
    if server is None or getattr(server, '_freevideo_launcher', None) is None:
        return ''
    return '' if getattr(server, '_freevideo_page_reported', None) else PAGE_SCRIPTS_MISSING


def save_page_report(engine_root, report):
    """The latest page check and a short history, for diagnostic reports."""
    logs = Path(engine_root) / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    line = json.dumps(report, ensure_ascii=False)
    temporary = logs / 'page-check.json.tmp'
    temporary.write_text(line, encoding='utf-8')
    os.replace(temporary, logs / 'page-check.json')
    history = logs / 'page-checks.jsonl'
    rows = history.read_text(encoding='utf-8').splitlines()[-19:] if history.is_file() else []
    history.write_text('\n'.join([*rows, line]) + '\n', encoding='utf-8')


def register():
    from aiohttp import web
    import folder_paths
    from server import PromptServer
    from .comfy_bridge import source_root, installation_root
    server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_launcher', None):
        return
    # Capture the loaded source now. Replacing files does not make an old live
    # server eligible for opening a new-version workflow without restarting.
    source = source_root().resolve()
    root = Path(folder_paths.__file__).resolve().parent
    info = dict(protocol=1, source=str(source), engine_root=str(installation_root(source)),
                comfy_root=str(root), data_root=str(Path(folder_paths.base_path).resolve()))
    from .comfy_console import install
    try:
        info['console_log'] = str(install(info['engine_root']))
    except (OSError, ValueError) as error:
        info['console_error'] = 'Could not capture ComfyUI output: ' + str(error)
    server._freevideo_launcher = info

    @server.routes.get('/freevideo/launcher')
    async def launcher(request):
        return web.json_response(info)

    @server.routes.get('/freevideo/launcher/workflow')
    async def workflow(request):
        value = json.loads((source / 'example_workflows' / 'FreeVideo-All-in-One.json').read_text(encoding='utf-8'))
        return web.json_response(value)

    # web/health.js posts what it found; the launcher shows a failure to the
    # user. The latest page wins, which is the page the user is looking at.
    page = {}

    @server.routes.post('/freevideo/launcher/ui')
    async def page_check(request):
        try:
            report = page_report(await request.json())
        except ValueError:
            report = None
        if report is None:
            return web.json_response(dict(error='Expected {"state": "loading" | "ready" | "error", "problems": [...]}'), status=400)
        report['source'] = info['source']
        page.clear(); page.update(report)
        server._freevideo_page_reported = report['at']
        try:
            save_page_report(info['engine_root'], report)
        except OSError as error:
            logging.warning('[FreeVideo] Could not save the page check: %s', error)
        if report['state'] == 'error':
            logging.warning('[FreeVideo] The browser page did not load FreeVideo completely: %s (%s)',
                            ' | '.join(report['problems']) or 'no details', report['agent'])
        return web.json_response(dict(ok=True))

    @server.routes.get('/freevideo/launcher/ui')
    async def page_state(request):
        return web.json_response(page)
