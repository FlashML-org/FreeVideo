"""Qt Quick entry point. Importing this module does not import Qt or Torch."""
import json
import os
from pathlib import Path
import sys
import time
import traceback


def theme():
    """branding.py's palette for QML, so the launcher and ComfyUI views match.

    QML reads '#AARRGGBB'; translucent values are derived from the table
    rather than added as new shades. Pixel sizes are Qt Quick's own scale.
    """
    from . import branding as b
    return dict(
        bg=b.BACKGROUND, canvas=b.CANVAS, surface=b.SURFACE, raised=b.RAISED, hover=b.HOVER,
        border=b.BORDER, sheen=b.SHEEN, text=b.TEXT, muted=b.MUTED, disabled=b.DISABLED,
        accent=b.ACCENT, accentHover=b.ACCENT_HOVER, accentRamp=b.ACCENT_RAMP,
        accentDim=b.ACCENT_DIM, accentSubtle=b.ACCENT_SUBTLE,
        success=b.SUCCESS, successSubtle=b.SUCCESS_SUBTLE, warning=b.WARNING,
        danger=b.DANGER, dangerSubtle=b.DANGER_SUBTLE, dangerLine='#66' + b.DANGER[1:],
        scrim='#cc' + b.BACKGROUND[1:], shadow='#66000000',
        micro=12, body=14, strong=16, section=20, hero=30,
        # Radius grows with the object: glyph, control, card, sheet; heights
        # are compact, standard and the one main action, as in theme.css.
        radiusXs=3, radiusSm=b.RADIUS_SM, radiusMd=b.RADIUS_MD, radiusLg=b.RADIUS_LG,
        heightSm=32, height=36, heightLg=44, mono=b.MONO)


def create_ui(session, *, show=True):
    from .windows_ux import taskbar_identity
    taskbar_identity()
    from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot, Qt
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication, QFileDialog
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
    from PySide6.QtQuickControls2 import QQuickStyle
    # The launcher must not consume the GPU used for video generation.
    QQuickWindow.setGraphicsApi(QSGRendererInterface.Software)
    QQuickStyle.setStyle('Basic')
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName('FreeVideo')
    app.setOrganizationName('FreeVideo')
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    font = QFont()
    font.setFamilies(['Segoe UI', 'Microsoft YaHei UI', 'Noto Sans', 'Noto Sans CJK SC', 'sans-serif'])
    font.setPointSize(10)
    app.setFont(font)
    icon = QIcon(str(Path(__file__).parent / 'assets/icon.ico'))
    if icon.isNull():
        icon = QIcon(str(Path(__file__).parent / 'assets/icon.png'))
    app.setWindowIcon(icon)

    class Bridge(QObject):
        changed = Signal()
        def __init__(self):
            super().__init__(engine)
            self.value = session.snapshot()
            self.timer = QTimer(self)
            self.timer.setInterval(250)
            self.timer.timeout.connect(self.refresh)
            self.timer.start()

        @Property('QVariantMap', notify=changed)
        def state(self):
            return self.value

        def invoke(self, function, *args):
            try:
                function(*args)
            except Exception:
                session.error = traceback.format_exc()
            self.refresh()

        @Slot()
        def refresh(self):
            try:
                session.tick()
                value = session.snapshot()
                if value != self.value:
                    self.value = value
                    self.changed.emit()
                if session.closing:
                    self.timer.stop()
                    app.quit()
            except Exception:
                # A failed status read must remain copyable and not kill the UI.
                session.error = traceback.format_exc()
                self.value = dict(self.value, error=session.error)
                self.changed.emit()

        @Slot(str, 'QVariant')
        def edit(self, key, value):
            self.invoke(session.edit, key, value)

        @Slot(str, bool)
        def action(self, name, accepted=False):
            self.invoke(session.action, name, accepted)

        @Slot(str)
        def browse(self, key):
            if session.controller.busy:
                return
            path = QFileDialog.getExistingDirectory(None, session.t('Choose folder', '选择文件夹'),
                str(session.form.get(key) or ''), QFileDialog.ShowDirsOnly)
            if path:
                self.invoke(session.add_folder, path) if key == 'model_dirs' else self.invoke(session.edit, key, path)

        @Slot()
        def browsePackages(self):
            if session.controller.busy or session.importer.busy:
                return
            paths, _ = QFileDialog.getOpenFileNames(None, session.t('Import offline packages', '导入离线包'),
                                                    '', 'FreeVideo (*.zip)')
            if paths:
                self.invoke(session.import_packages, paths)

        @Slot()
        def clearRuntime(self):
            self.invoke(session.clear_runtime)

        @Slot()
        def browseRuntimePackage(self):
            if session.controller.busy or session.importer.busy:
                return
            paths, _ = QFileDialog.getOpenFileNames(None, session.t('Choose environment ZIP', '选择运行环境包'),
                                                    '', 'FreeVideo (*.zip)')
            if paths:
                self.invoke(session.import_packages, paths)

        @Slot('QVariantList')
        def importPackages(self, urls):
            paths = []
            for value in urls:
                url = value if isinstance(value, QUrl) else QUrl(str(value))
                if not url.isLocalFile():
                    session.error = session.t('Drop downloaded ZIP files here.', '请拖入已下载到本机的 ZIP 文件。')
                    self.refresh()
                    return
                paths.append(url.toLocalFile())
            if paths:
                self.invoke(session.import_packages, paths)

        @Slot(int)
        def removeFolder(self, index):
            self.invoke(session.remove_folder, index)

        @Slot(str)
        def copy(self, value):
            app.clipboard().setText(value)

        @Slot()
        def copyLog(self):
            self.invoke(lambda: app.clipboard().setText(session.full_log()))

        @Slot()
        def exportReport(self):
            if session.report['status'] == 'running':
                return
            path, _ = QFileDialog.getSaveFileName(None, session.t('Export report', '导出报告'),
                'freevideo-diagnostics-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '.zip',
                'ZIP (*.zip)')
            if path:
                if not path.lower().endswith('.zip'):
                    path += '.zip'
                self.invoke(session.export_report, path)

        @Slot(str)
        def link(self, value):
            from .windows_ux import open_browser
            self.invoke(open_browser, value)

        @Slot(str)
        def selectSource(self, value):
            self.invoke(session.select_source, value)

        @Slot(str)
        def selectProxyMode(self, value):
            self.invoke(session.select_proxy_mode, value)

        @Slot()
        def speedTest(self):
            self.invoke(session.speed_test)

        @Slot(int, bool)
        def compatibility(self, level, automatic):
            self.invoke(session.set_compatibility, level, automatic)

        @Slot(str)
        def update(self, token):
            self.invoke(session.update, token)

        @Slot()
        def dismissUpdate(self):
            self.invoke(session.dismiss_update)

        @Slot(str)
        def checkUpdates(self, token):
            self.invoke(session.check_update, token)

        @Slot(str)
        def terminal(self, path):
            session.log_source = path
            self.refresh()

        @Slot()
        def clearTerminal(self):
            session.tail.clear()
            self.refresh()

        @Slot()
        def dismissNotice(self):
            self.invoke(session.dismiss_notice)

        @Slot()
        def close(self):
            self.invoke(session.close)

        @Slot()
        def shutdown(self):
            self.timer.stop()
            session.close()

    from .model_guidance import links, cloud_models
    from .launcher_copy import display
    engine = QQmlApplicationEngine()
    bridge = Bridge()
    engine.rootContext().setContextProperty('backend', bridge)
    engine.rootContext().setContextProperty('initialState', bridge.value)
    # Nested Python tuples remain opaque objects in QML. Named maps become
    # QVariantMaps, so both the displayed label and clicked URL are available.
    engine.rootContext().setContextProperty('modelLinks', {
        component: [dict(label=label, label_zh=display(label, True), url=url) for label, url in rows]
        for component, rows in links().items()})
    engine.rootContext().setContextProperty('cloudLinks', cloud_models())
    engine.rootContext().setContextProperty('theme', theme())
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / 'launcher/Main.qml')))
    if not engine.rootObjects():
        bridge.timer.stop()
        raise RuntimeError('FreeVideo desktop layout could not be loaded')
    window = engine.rootObjects()[0]
    window.setIcon(icon)
    available = app.primaryScreen().availableGeometry()
    width = min(1440, int(available.width() * .86))
    height = min(1020, int(available.height() * .88))
    window.setMinimumWidth(min(780, width))
    window.setMinimumHeight(min(540, height))
    window.resize(width, height)
    window.setPosition(available.x()+(available.width()-width)//2, available.y()+(available.height()-height)//2)
    window.setVisible(show)
    app.aboutToQuit.connect(bridge.shutdown)
    # Keep Python-owned objects alive as long as the QML engine.
    engine.bridge = bridge
    engine.session = session
    return app, engine, window, bridge


def smoke_test(app, engine, window, bridge, destination):
    """Exercise the packaged QML, manifests and managed-Python entry point."""
    import queue
    from PySide6.QtCore import QTimer
    from .desktop_runtime import check_launcher_payload, check_launcher_reopen
    from .comfy_launcher_runtime import LauncherRunner
    from .launcher_update import current_build
    from .monitoring import save
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    session = engine.session
    try:
        payload = check_launcher_payload(session.source, destination)
        payload['reopen'] = check_launcher_reopen(destination)
        from PySide6.QtQuick import QQuickItem
        for page in ('comfy', 'models', 'progress', 'launcher'):
            session.page = page
            bridge.refresh()
            app.processEvents()
            primary = window.findChild(QQuickItem, 'primaryButton')
            if primary is None or not primary.isVisible() or primary.width() < 40:
                raise RuntimeError('Packaged launcher action is not visible: ' + page)
        window.setProperty('settingsOpen', True)
        app.processEvents()
        window.setProperty('settingsOpen', False)
        session.page = 'comfy'
        bridge.refresh()
        payload.update(wizard_pages=3, launcher_page=True, download_controls=True, renderer='Qt Quick/software')
        build = current_build()
        if getattr(sys, 'frozen', False) and build is None:
            raise ValueError('Packaged launcher update identity is missing')
        events = queue.Queue()
        runner = LauncherRunner(session.source, events, destination / 'help')
        runner.start('help', destination / 'engine', ['--help'])
    except Exception:
        save(destination / 'smoke.json', dict(success=False, error=traceback.format_exc()))
        app.quit()
        return
    deadline = time.monotonic() + 240
    timer = QTimer(bridge)
    def finish():
        if runner.busy and time.monotonic() < deadline:
            return
        result = None
        while not events.empty():
            kind, value = events.get_nowait()
            if kind == 'done':
                result = value
        imported = [p for p in ('torch', 'triton', 'transformers', 'tkinter') if p in sys.modules]
        save(destination / 'smoke.json', dict(success=bool(result and result['status'] == 'complete' and not imported),
            task=result, interface='Qt Quick launcher', build=build, source=str(session.source),
            payload=payload, model_modules_imported=imported))
        window.grabWindow().save(str(destination / 'launcher.png'))
        if runner.busy:
            runner.cancel()
        timer.stop()
        app.quit()
    timer.timeout.connect(finish)
    timer.start(100)


def main(session=None):
    # Linux process supervision reexecutes sys.executable. A frozen GUI must
    # dispatch this helper before creating another application window.
    if (getattr(sys, 'frozen', False) and sys.platform == 'linux'
            and sys.argv[1:4] == ['-B', '-m', 'freevideo_engine.linux_process_host']):
        from .linux_process_host import main as supervise
        sys.argv = [sys.argv[3], *sys.argv[4:]]
        raise SystemExit(supervise())
    smoke = '--smoke-test' in sys.argv
    from .desktop_runtime import launcher_root
    portable_root = Path(sys.executable).parent
    if session is None and getattr(sys, 'frozen', False) and (portable_root / 'portable.json').is_file() and not smoke:
        from .portable_launcher import portable_session
        session = portable_session(portable_root)
    if not smoke and session is None:
        from .launcher_update import current_build, forward_approved
        identity = current_build()
        if identity and forward_approved(identity, launcher_root()):
            return
    if os.name == 'nt':
        # Console helpers inherit one hidden console instead of opening CMD windows.
        import ctypes
        lib = ctypes.windll.kernel32
        lib.GetConsoleWindow.restype = ctypes.c_void_p
        if not lib.GetConsoleWindow():
            lib.AllocConsole()
            ctypes.windll.user32.ShowWindow(ctypes.c_void_p(lib.GetConsoleWindow()), 0)
    from .launcher_session import Session
    from .launcher_settings import Store
    if session is None:
        session = Session(smoke=smoke, store=Store(launcher_root(),
            documents=launcher_root() / 'smoke-documents' if smoke else None))
    app, engine, window, bridge = create_ui(session)
    if smoke:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: smoke_test(app, engine, window, bridge,
            sys.argv[sys.argv.index('--smoke-test')+1]))
    try:
        app.exec()
    finally:
        bridge.shutdown()
        # Destroy the QML tree before its Python context properties.
        engine.deleteLater()
        app.processEvents()


if __name__ == '__main__':
    main()
