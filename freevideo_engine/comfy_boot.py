"""FreeVideo's opening screen from the page's first frame.

ComfyUI serves its own index.html; FreeVideo's interface arrives later, as an extension. Without help, a visit that
opens FreeVideo first shows ComfyUI's loading screen and only then FreeVideo. This module adds a small inline style
and script at the very start of that page's <head>. When the visit opens FreeVideo (the launcher's
``?freevideo=launch``, or a refresh of such a tab, unless its last view was the node view), the
first frame is FreeVideo's ground with its top bar outline, and ComfyUI stays out of sight until FreeVideo's shell has
put its first view in front (shell.js calls ``__freevideoBoot.done()``). If FreeVideo's scripts never load, ComfyUI is
shown after 10 s with a short note, so nobody is left in front of an empty screen. Any other way of opening ComfyUI
(a bare address, another launcher) is not changed: only the FreeVideo launcher opens FreeVideo.
"""
import logging
import os

MARK = 'freevideo-boot'

# Kept small: it runs before anything else on the page. Colours and places follow the shell's top bar (shell.js
# stores its ground colour in comfy-splash-bg; the bar's wordmark, view names and status capsule sit where they are
# drawn here, at 1440 and at phone width).
BOOT_HTML = (
    '<style id="freevideo-boot-style">'
    'html.fv-boot{background:var(--fv-boot-bg,#141518)}'
    'html.fv-boot #splash-loader{visibility:hidden!important}'
    '#fv-boot{position:fixed;inset:0;z-index:2147483646;background:var(--fv-boot-bg,#141518);'
    'transition:opacity .2s cubic-bezier(.2,.8,.2,1)}'
    '#fv-boot.fv-boot-out{opacity:0;pointer-events:none}'
    '#fv-boot img{position:absolute;left:20px;top:18px;height:20px;width:auto;user-select:none;-webkit-user-drag:none}'
    '#fv-boot i{position:absolute;display:block;border-radius:999px;background:var(--fv-boot-pill)}'
    '#fv-boot .fv-boot-tabs{left:50%;top:8px;width:392px;height:40px;transform:translateX(-50%)}'
    '#fv-boot .fv-boot-status{right:225px;top:8px;width:68px;height:40px}'
    '@media (max-width:719px){#fv-boot img{left:18px;top:20px;height:16px}'
    '#fv-boot .fv-boot-tabs{top:6px;width:100px;height:44px}#fv-boot .fv-boot-status{display:none}}'
    '.fv-boot-note{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:2147483647;max-width:calc(100vw - 32px);'
    'box-sizing:border-box;display:flex;gap:12px;align-items:center;padding:10px 16px;border-radius:12px;background:#1c1d20;'
    'color:#f3f5f8;font:13px/1.5 system-ui,sans-serif;box-shadow:0 8px 32px #0006}'
    '.fv-boot-note button{flex:none;border:1px solid #ffffff2e;border-radius:8px;background:transparent;color:inherit;'
    'font:inherit;padding:4px 12px;cursor:pointer}'
    '</style>'
    '<script id="freevideo-boot">(function(){'
    'var d=document,r=d.documentElement,on=false,bg="#141518";'
    'try{var q=new URLSearchParams(location.search),v=localStorage.getItem("freevideo.view");'
    'on=(q.get("freevideo")==="launch"||sessionStorage.getItem("freevideo.launched")==="1")&&v!=="nodes";'
    'bg=localStorage.getItem("comfy-splash-bg")||bg}'
    'catch(e){on=/[?&]freevideo=launch(&|$)/.test(location.search)}'
    'if(!on)return;'
    'if(!/^#[0-9a-f]{6}$/i.test(bg))bg="#141518";'
    'var n=parseInt(bg.slice(1),16),light=(0.2126*(n>>16&255)+0.7152*(n>>8&255)+0.0722*(n&255))>140;'
    'r.style.setProperty("--fv-boot-bg",bg);'
    'r.style.setProperty("--fv-boot-pill",light?"rgba(0,0,0,.06)":"rgba(255,255,255,.045)");'
    'r.classList.add("fv-boot");'
    'var el=d.createElement("div");el.id="fv-boot";el.setAttribute("aria-hidden","true");'
    'el.innerHTML=\'<img alt="" src="extensions/FreeVideo/assets/freevideo\'+(light?"-light":"")+\'.svg">'
    '<i class="fv-boot-tabs"></i><i class="fv-boot-status"></i>\';'
    'r.appendChild(el);'
    'var b=window.__freevideoBoot={active:true,done:function(){'
    'if(!b.active)return;b.active=false;el.classList.add("fv-boot-out");'
    'setTimeout(function(){el.remove();r.classList.remove("fv-boot")},220)}};'
    'function note(){var zh=/^zh/i.test(navigator.language||""),p=d.createElement("div");p.className="fv-boot-note";'
    'p.setAttribute("role","status");p.textContent=zh?"FreeVideo 界面没有及时打开，已显示 ComfyUI。":'
    '"FreeVideo did not open in time, so ComfyUI is shown.";var k=d.createElement("button");k.type="button";'
    'k.textContent=zh?"重新加载":"Reload";k.onclick=function(){location.reload()};p.appendChild(k);'
    '(d.body||r).appendChild(p);setTimeout(function(){p.remove()},12000)}'
    'function late(){if(b.active){b.done();note()}}'
    'setTimeout(function(){if(!b.active)return;if(!window.FreeVideoShell)late();else setTimeout(late,20000)},10000)'
    '})();</script>'
)


def inject(html):
    """The page with the opening screen first in <head>; unchanged when it has no <head> or already has it."""
    if MARK in html:
        return html
    lower = html.lower()
    start = lower.find('<head')
    if start < 0:
        return html
    end = lower.find('>', start)
    if end < 0:
        return html
    return html[:end + 1] + BOOT_HTML + html[end + 1:]


class BootPage:
    """ComfyUI's index.html with the opening screen, read again only when the file changes."""

    def __init__(self, root):
        self.root = root
        self.key = None
        self.body = None

    def page(self):
        path = os.path.join(self.root, 'index.html')
        info = os.stat(path)
        key = (path, info.st_mtime_ns, info.st_size)
        if key != self.key:
            with open(path, encoding='utf-8') as source:
                self.body = inject(source.read()).encode('utf-8')
            self.key = key
        return self.body


def middleware_for(root):
    from aiohttp import web
    boot = BootPage(root)
    failed = []

    @web.middleware
    async def boot_screen(request, handler):
        response = await handler(request)
        if request.method != 'GET' or request.path not in ('/', '/index.html') or response.status != 200:
            return response
        try:
            body = boot.page()
        except Exception as error:  # ComfyUI's own page is always better than none.
            if not failed:
                failed.append(error)
                logging.warning('[FreeVideo] Opening screen not added to the page: %s', error)
            return response
        headers = {name: value for name, value in response.headers.items()
                   if name.lower() in ('cache-control', 'pragma', 'expires')}
        return web.Response(body=body, content_type='text/html', charset='utf-8', headers=headers)

    return boot_screen


def register(server=None):
    """Add the opening screen to ComfyUI's page; called while the custom node loads, before the server starts."""
    if server is None:
        from server import PromptServer
        server = PromptServer.instance
    if server is None or getattr(server, '_freevideo_boot', False):
        return False
    root = getattr(server, 'web_root', None)
    app = getattr(server, 'app', None)
    if not root or app is None or app.frozen:
        logging.warning('[FreeVideo] Opening screen not added: the page server is not open for additions')
        return False
    app.middlewares.append(middleware_for(root))
    server._freevideo_boot = True
    return True
