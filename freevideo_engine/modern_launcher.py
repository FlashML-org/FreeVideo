"""Qt Quick entry point. Importing this module does not import Qt or Torch."""
import json
import os
from pathlib import Path
import sys
import time
import traceback

from .long_paths import io_path, plain


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


def multimedia_available():
    """Whether a release video can play in the notes; otherwise they show its cover (a GIF or WebP still moves).

    Qt Multimedia must be present, and the window drawn by a graphics API: VideoOutput draws nothing in the
    software renderer, which the launcher uses to keep the GPU free, so there the cover is shown instead of a
    still picture with a speaker."""
    try:
        from PySide6.QtQuick import QQuickWindow
        if QQuickWindow.sceneGraphBackend() == 'software':
            return False
        import PySide6.QtMultimedia  # noqa: F401  (also loads its system libraries)
        from PySide6.QtCore import QLibraryInfo
        return (Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath)) / 'QtMultimedia').is_dir()
    except Exception:  # Missing module or library: the cover stays.
        return False


def launcher_icon():
    from PySide6.QtGui import QIcon
    if sys.platform == 'darwin' and getattr(sys, 'frozen', False):
        # Keep the padded native icon after Qt takes ownership of the Dock icon.
        icon = QIcon(str(Path(sys.executable).parent.parent / 'Resources/FreeVideo.icns'))
        if not icon.isNull():
            return icon
    icon = QIcon(str(Path(__file__).parent / 'assets/icon.ico'))
    if icon.isNull():
        icon = QIcon(str(Path(__file__).parent / 'assets/icon.png'))
    return icon


def tray_available():
    from PySide6.QtWidgets import QSystemTrayIcon
    return QSystemTrayIcon.isSystemTrayAvailable()


def instance_name():
    import hashlib
    from .desktop_runtime import launcher_root
    return 'FreeVideo-launcher-' + hashlib.sha1(plain(launcher_root()).encode('utf-8')).hexdigest()[:16]


def hand_to_running_instance(name):
    """Ask a launcher already running for this installation to show its window; True once it is there.

    A launcher that is closing says so, so a start during an update hand-over still opens its window.
    One that accepts the connection but is too busy to answer is still running: it keeps its window."""
    try:
        from PySide6.QtNetwork import QLocalSocket
    except ImportError:
        return False
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(500):
        return False
    if sys.platform == 'win32':
        # This start holds the foreground right from the click; hand it over, so the window shown
        # comes to the front instead of only flashing on the taskbar.
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except (AttributeError, OSError):
            pass
    socket.write(b'show')
    socket.waitForBytesWritten(500)
    reply = bytes(socket.readAll()) if socket.waitForReadyRead(5000) else b'busy'
    socket.disconnectFromServer()
    return reply in (b'ok', b'busy')


def serve_instance(app, session, bridge, name):
    """Answer later starts of the launcher by showing this window instead of starting a second one."""
    try:
        from PySide6.QtNetwork import QLocalServer
    except ImportError:
        return None
    server = QLocalServer(app)
    if not server.listen(name):
        # A leftover socket file from a launcher that died is stale; one that still accepts connections is not.
        if hand_to_running_instance(name):
            return None
        QLocalServer.removeServer(name)
        if not server.listen(name):
            return None
    def accept():
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            pending = bytearray()
            def ready(socket=socket, pending=pending):
                pending.extend(bytes(socket.readAll()))
                if len(pending) < 4:
                    return
                if bytes(pending) == b'show' and not session.closing:
                    bridge.show_window()
                    socket.write(b'ok')
                else:
                    socket.write(b'closing' if session.closing else b'no')
                socket.flush()
                socket.disconnectFromServer()
            socket.readyRead.connect(ready)
            socket.disconnected.connect(socket.deleteLater)
    server.newConnection.connect(accept)
    return server


def create_ui(session, *, show=True, instance=None):
    from .launcher_session import log_error
    from .windows_ux import taskbar_identity
    taskbar_identity()
    from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot, Qt
    from PySide6.QtGui import QFont
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
    families = ['Segoe UI', 'Microsoft YaHei UI', 'Noto Sans', 'Noto Sans CJK SC']
    # The Linux AppImage carries the launcher's Chinese characters for desktops
    # without a Chinese font; a complete system font still comes first.
    from PySide6.QtGui import QFontDatabase
    for path in sorted((io_path(Path(__file__).parent) / 'assets/fonts').glob('*.otf')):
        for family in QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(plain(path))):
            if family not in families:
                families.append(family)
    font.setFamilies(families + ['sans-serif'])
    font.setPointSize(10)
    app.setFont(font)
    icon = launcher_icon()
    app.setWindowIcon(icon)

    class Bridge(QObject):
        changed = Signal()
        downloadsChanged = Signal()
        def __init__(self):
            super().__init__(engine)
            self.alerted = None
            self.attended = session.attention
            self.tray = None
            self.menu = None
            self.value = session.snapshot()
            self.download_value = session.downloads_view()
            self.timer = QTimer(self)
            self.timer.setInterval(250)
            self.timer.timeout.connect(self.refresh)
            self.timer.start()

        @Property('QVariantMap', notify=changed)
        def state(self):
            return self.value

        @Property('QVariantMap', notify=downloadsChanged)
        def downloadList(self):
            return self.download_value

        def invoke(self, function, *args, **kwargs):
            try:
                function(*args, **kwargs)
            except Exception:
                session.error = traceback.format_exc()
                log_error(session.error)
            self.refresh()

        @Slot()
        def refresh(self):
            try:
                session.tick()
                value = session.snapshot()
                if value != self.value:
                    self.value = value
                    self.changed.emit()
                downloads = session.downloads_view()
                if downloads is not self.download_value:
                    self.download_value = downloads
                    self.downloadsChanged.emit()
                update = value.get('update') or {}
                if update.get('remind') and update.get('key') != self.alerted and engine.rootObjects():
                    # Flash the taskbar button once per new reminder when the
                    # launcher sits behind the browser.
                    self.alerted = update['key']
                    window = engine.rootObjects()[0]
                    if not window.isActive():
                        window.alert(0)
                if session.attention != self.attended and engine.rootObjects():
                    # The app asked for the launcher (adding the video model): bring it in front of the browser.
                    self.attended = session.attention
                    window = engine.rootObjects()[0]
                    window.showNormal(); window.raise_(); window.requestActivate()
                if session.closing:
                    self.timer.stop()
                    app.quit()
            except Exception:
                # A failed status read must remain copyable and not kill the UI.
                session.error = traceback.format_exc()
                log_error(session.error)
                from .offline_errors import alias_failure
                if alias_failure(session.error):
                    # The card says what to do; the details keep the cause.
                    from .failure_details import launcher_failure
                    zh = session.language.startswith('zh')
                    value = dict(self.value, error=session.error, failure=launcher_failure(session.error, zh=zh))
                else:
                    value = dict(self.value, error=session.error)
                if value != self.value:
                    self.value = value
                    self.changed.emit()

        @Slot(str, 'QVariant')
        def edit(self, key, value):
            def edit_field():
                try:
                    session.edit(key, value)
                except ValueError as exc:
                    if key not in ('token', 'url'):
                        raise
                    session.field_error = dict(key=key, message=str(exc))
            self.invoke(edit_field)

        @Slot(str, bool)
        def action(self, name, accepted=False):
            self.invoke(session.action, name, accepted)

        @Slot(str)
        def useDisk(self, folder):
            self.invoke(session.use_disk, folder)

        @Slot(bool)
        def cleanupDownloads(self, remove=False):
            self.invoke(session.cleanup_downloads, remove)

        @Slot()
        def upgradeModel(self):
            self.invoke(session.upgrade_model)

        @Slot(str)
        def imagePackAction(self, action):
            self.invoke(session.image_pack_action, action)

        @Slot(str)
        def promptModelAction(self, action):
            self.invoke(session.prompt_model_action, action)

        @Slot()
        def dismissPromptHint(self):
            self.invoke(session.dismiss_prompt_hint)

        @Slot()
        def cancelModelUpgrade(self):
            self.invoke(session.cancel_model_upgrade)

        @Slot()
        def dismissModelUpgrade(self):
            self.invoke(session.dismiss_model_upgrade)

        @Slot(str)
        def browse(self, key):
            if not session.can_manage():
                self.invoke(session.explain_busy)
                return
            path = QFileDialog.getExistingDirectory(None, session.t('Choose folder', '选择文件夹'),
                str(session.form.get(key) or ''), QFileDialog.ShowDirsOnly)
            if path:
                self.invoke(session.add_folder, path) if key == 'model_dirs' else self.invoke(session.edit, key, path)

        @Slot(str)
        def setDownloadScope(self, scope):
            self.invoke(session.set_download_scope, scope)

        @Slot()
        def prepareDownloadList(self):
            self.invoke(session.prepare_download_list)

        @Slot()
        def exportDownloadList(self):
            if session.controller.busy and session.download_scope != 'image':
                return
            path = QFileDialog.getExistingDirectory(None, session.downloads_view()['copy']['export'], '', QFileDialog.ShowDirsOnly)
            if path:
                self.invoke(session.export_downloads, path)

        @Slot()
        def useDownloadedFolder(self):
            if not session.can_manage():
                self.invoke(session.explain_busy)
                return
            path = QFileDialog.getExistingDirectory(None, session.downloads_view()['copy']['use_folder'], '', QFileDialog.ShowDirsOnly)
            if path:
                self.invoke(session.use_downloaded_folder, path)

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
                    # A hint, not a failure: the card stays as it was.
                    session.action_notice = 'drop_local_zip'
                    self.refresh()
                    return
                paths.append(url.toLocalFile())
            if paths:
                self.invoke(session.import_packages, paths)

        @Slot(int)
        def removeFolder(self, index):
            self.invoke(session.remove_folder, index)

        @Slot(str)
        def setLanPassword(self, value):
            self.invoke(session.change_lan_password, value)

        @Slot()
        def removeLanPassword(self):
            self.invoke(session.change_lan_password, None)

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
                # The dialog asked before replacing the name it returned; a name we extend was never asked about.
                from .launcher_session import report_target
                path, confirmed = report_target(path)
                self.invoke(session.export_report, path, confirmed)

        @Slot(str)
        def link(self, value):
            # A licence may be a text file on this computer rather than a web page.
            from .component_licenses import is_file
            from .windows_ux import open_browser, open_text
            if is_file(value):
                # A text inside the installation in use is opened through that installation's folder.
                engine = session.installation_engine()
                self.invoke(open_text, value, **(dict(engine=engine) if engine else {}))
            else:
                self.invoke(open_browser, value)

        @Slot()
        def wantReleaseMedia(self):
            # The notes window opened: the current version's picture or video may come now. The window calls this while
            # it is being shown, so the new state follows on the next turn rather than inside that change.
            if session.release_media_wanted:
                return
            session.release_media_wanted = True
            QTimer.singleShot(0, self.refresh)

        @Slot(str, str)
        def reportProblem(self, error, title):
            # The button shows "opened" at once and the form follows on the next turn, so a slow browser is not met with a second click.
            if not session.start_report(error, title):
                return
            self.refresh()
            QTimer.singleShot(0, lambda: self.openReport(error, title))

        def openReport(self, error, title):
            from . import __version__
            from .launcher_issue import adapter_line, environment_line, issue_url, machine_hardware, product_version
            from .windows_ux import open_browser
            # The release (0.4.x) is named, not the build number; only fields that carry no path or user name travel.
            version = session.installed_product_version() or product_version(__version__)
            engine = session.installation_engine()
            url = issue_url(error, version, title, zh=session.language.startswith('zh'),
                            environment=environment_line(version, machine_hardware(engine) if engine else {}),
                            diagnostics=[adapter_line()])
            self.invoke(open_browser, url)

        @Slot()
        def openInstallFolder(self):
            from PySide6.QtGui import QDesktopServices
            # The package the launcher verifies is the folder it runs from; the installation's engine folder is the fallback.
            folder = Path(session.source)
            if not folder.is_dir():
                engine = session.installation_engine()
                folder = Path(engine).parent if engine else folder
            if sys.platform.startswith('linux'):
                from .windows_ux import open_folder
                if not open_folder(folder.resolve()):
                    session.action_notice = 'folder_open_failed'
                    self.refresh()
                return
            self.invoke(QDesktopServices.openUrl, QUrl.fromLocalFile(str(folder.resolve())))

        @Slot()
        def openDownloadFolder(self):
            from PySide6.QtGui import QDesktopServices
            folder = session.download_export
            if folder and Path(folder).is_dir():
                if session.action_notice == 'download_missing':
                    session.action_notice = ''
                if sys.platform.startswith('linux'):
                    from .windows_ux import open_folder
                    if not open_folder(Path(folder).resolve()):
                        session.action_notice = 'folder_open_failed'
                        self.refresh()
                    return
                self.invoke(QDesktopServices.openUrl, QUrl.fromLocalFile(str(Path(folder).resolve())))
            else:
                session.action_notice = 'download_missing'
                self.refresh()

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

        @Slot(int)
        def compatibilityLevel(self, level):
            self.invoke(session.set_compatibility_level, level)

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

        @Slot(result=str)
        def closeChoice(self):
            # Closing already decided (an update hand-over, or Exit chosen): nothing more to ask.
            if session.closing:
                return ''
            try:
                return session.close_choice()
            except Exception:
                return ''

        @Slot(result=str)
        def returnPlace(self):
            """Where the window can be reopened from while it is in the background."""
            if not tray_available():
                return 'taskbar'
            return 'menubar' if sys.platform == 'darwin' else 'tray'

        @Slot()
        def runInBackground(self):
            """Hide the window and leave the launcher, with the ComfyUI it started, running."""
            self.hide_to_background()

        @Slot()
        def close(self):
            self.invoke(session.close)

        @Slot()
        def shutdown(self):
            self.timer.stop()
            if self.tray is not None:
                self.tray.hide()
            session.close()

        def show_window(self):
            """Bring the window back from the background (tray, Dock, or a second start of the launcher)."""
            if self.tray is not None:
                self.tray.hide()
            window = engine.rootObjects()[0]
            window.showNormal()
            window.raise_()
            window.requestActivate()

        def hide_to_background(self):
            window = engine.rootObjects()[0]
            if not tray_available():
                # No tray to return from: minimise, so the window stays reachable on the taskbar.
                window.showMinimized()
                return
            if self.tray is None:
                self.tray = self.make_tray()
            app.setQuitOnLastWindowClosed(False)
            self.tray.show()
            window.hide()

        def make_tray(self):
            from PySide6.QtGui import QAction
            from PySide6.QtWidgets import QMenu, QSystemTrayIcon
            tray = QSystemTrayIcon(icon, self)
            tray.setToolTip('FreeVideo')
            menu = QMenu()
            open_action = QAction(session.t('Open FreeVideo', '打开 FreeVideo'), menu)
            exit_action = QAction(session.t('Exit', '退出'), menu)
            open_action.triggered.connect(self.show_window)
            exit_action.triggered.connect(self.exit_from_tray)
            menu.addAction(open_action)
            menu.addAction(exit_action)
            tray.setContextMenu(menu)
            tray.activated.connect(lambda reason: self.show_window() if reason in (
                QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
            self.menu = menu
            return tray

        def exit_from_tray(self):
            # The same questions as closing the window where work would be lost (a task of the
            # launcher, or a video being generated), asked in the window brought back; otherwise exit.
            from PySide6.QtCore import QMetaObject
            window = engine.rootObjects()[0]
            QMetaObject.invokeMethod(window, 'askExit')
            if window.property('closePending') or window.property('closeChoice'):
                self.show_window()

    from .model_guidance import cloud_models
    engine = QQmlApplicationEngine()
    bridge = Bridge()
    engine.rootContext().setContextProperty('backend', bridge)
    engine.rootContext().setContextProperty('initialState', bridge.value)
    engine.rootContext().setContextProperty('cloudLinks', cloud_models())
    engine.rootContext().setContextProperty('theme', theme())
    engine.rootContext().setContextProperty('multimedia', multimedia_available())
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / 'launcher/Main.qml')))
    if not engine.rootObjects():
        bridge.timer.stop()
        raise RuntimeError('FreeVideo desktop layout could not be loaded')
    window = engine.rootObjects()[0]
    window.setIcon(icon)
    bridge.instance = serve_instance(app, session, bridge, instance) if instance else None
    # Clicking the Dock icon (or any activation) of a launcher running in the background brings the window back.
    if sys.platform == 'darwin':
        app.applicationStateChanged.connect(lambda state: bridge.show_window()
            if state == Qt.ApplicationState.ApplicationActive and not window.isVisible()
            and not (bridge.menu is not None and bridge.menu.isVisible()) else None)
    if sys.platform == 'darwin':
        # Native traffic lights and resizing, with content behind the titlebar.
        # ApplicationWindow keeps controls inside the platform's safe area.
        window.setFlag(Qt.ExpandedClientAreaHint, True)
        window.setFlag(Qt.NoTitleBarBackgroundHint, True)
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


def save_screenshot(window, bridge, path):
    if not bridge.value.get('access_links'):
        window.grabWindow().save(str(path))


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
        from .linux_bundle import frozen_linux, child_environment
        if frozen_linux():
            import subprocess
            from .network import curl_command
            command = curl_command(5)
            version = subprocess.run([command[0], '--version'], env=child_environment(),
                                     capture_output=True, text=True, timeout=10, check=True).stdout
            if 'https' not in version:
                raise RuntimeError('Packaged curl lacks HTTPS support')
            payload['curl'] = dict(executable=command[0], version=version.splitlines()[0])
        from PySide6.QtQuick import QQuickItem
        for page in ('comfy', 'models', 'progress', 'launcher'):
            session.page = page
            bridge.refresh()
            app.processEvents()
            primary = window.findChild(QQuickItem, 'primaryButton')
            if primary is None or not primary.isVisible() or primary.width() < 40:
                raise RuntimeError('Packaged launcher action is not visible: ' + page)
            if page == 'progress':
                # The last setup step shows the finished ones with a check mark; keep it for the font check.
                save_screenshot(window, bridge, destination / 'launcher-steps.png')
        window.setProperty('settingsOpen', True)
        app.processEvents()
        window.setProperty('settingsOpen', False)
        # The licence list opens, and an offline runtime's licence texts open from the package itself.
        from PySide6.QtCore import QObject
        from . import component_licenses as licences
        window.setProperty('licensesOpen', True)
        app.processEvents()
        dialog = window.findChild(QObject, 'licensesDialog')
        if dialog is None or not dialog.property('visible'):
            raise RuntimeError('Packaged launcher licence list does not open')
        window.setProperty('licensesOpen', False)
        rows = licences.bundle(destination, dict(files=[dict(path=name) for name in ('cudart64_13.dll', 'cudnn64_9.dll', 'libiomp5md.dll')]))
        texts = {row['key']: row['link'] for row in rows[2:]}
        missing = {key: link for key, link in texts.items() if not licences.is_file(link) or not io_path(link).is_file()}
        if len(texts) != 4 or missing:
            raise RuntimeError('Packaged licence texts are missing: %r' % (missing or texts))
        session.page = 'comfy'
        bridge.refresh()
        payload.update(wizard_pages=3, launcher_page=True, download_controls=True, renderer='Qt Quick/software',
                       licence_texts=texts)
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
        save_screenshot(window, bridge, destination / 'launcher.png')
        if runner.busy:
            runner.cancel()
        timer.stop()
        app.quit()
    timer.timeout.connect(finish)
    timer.start(100)


def smoke_connect(app, engine, window, bridge, destination, values):
    """Connect the packaged launcher to an existing installation, as a person would, and open ComfyUI.

    Exercises what only a packaged launcher does on a real installation: its
    helper processes, the environment of the programs it starts, its source copy.
    """
    from PySide6.QtCore import QTimer
    from .comfy_launcher_runtime import server_info
    from .monitoring import save
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    session = engine.session
    for key, value in values.items():
        session.edit(key, value)
    session.action('primary')
    session.action('primary')
    deadline = time.monotonic() + 900
    timer = QTimer(bridge)
    def step():
        state = session.controller.state
        status = state.get('status')
        if session.controller.busy:
            return
        if status == 'review' and time.monotonic() < deadline:
            session.action('primary', True)
            return
        if status in ('open', 'failed', 'cancelled', 'restart-required') or time.monotonic() > deadline:
            info = server_info(state['url'].split('/?')[0]) if status == 'open' else {}
            save(destination / 'connect.json', dict(success=status == 'open' and info.get('status') == 'freevideo',
                                                    status=status, error=state.get('error'), url=state.get('url'), server=info))
            save_screenshot(window, bridge, destination / 'connected.png')
            timer.stop()
            app.quit()
    timer.timeout.connect(step)
    timer.start(250)


def main(session=None):
    if (getattr(sys, 'frozen', False) and sys.platform == 'darwin'
            and sys.argv[1:2] == ['--macos-process-host']):
        from .macos_process_host import main as supervise
        raise SystemExit(supervise(sys.argv[2:]))
    # A Mac app carries its own lightweight interpreter. Dispatch managed
    # children before Qt and run from the durable deployed source, so a fresh
    # Mac does not need a system Python or Homebrew merely to review setup.
    if (getattr(sys, 'frozen', False) and sys.platform == 'darwin'
            and len(sys.argv) >= 3 and sys.argv[1] == '--managed'):
        source = Path(sys.argv[2])
        if not (source / 'freevideo_engine/managed.py').is_file():
            raise ValueError('The managed launcher source is missing')
        sys.path.insert(0, str(source))
        import freevideo_engine
        freevideo_engine.__path__.insert(0, str(source / 'freevideo_engine'))
        from .managed import main as managed
        raise SystemExit(managed(sys.argv[3:]))
    if getattr(sys, 'frozen', False) and sys.platform.startswith('linux'):
        from .linux_bundle import prepare
        prepare()
        # Helpers such as Linux process supervision run this executable again
        # (processes.module_command); dispatch them before any window exists.
        if sys.argv[1:2] == ['--freevideo-module'] and len(sys.argv) >= 3:
            import runpy
            module = sys.argv[2]
            if module not in ('freevideo_engine.linux_process_host',):
                raise SystemExit('Unknown launcher helper: ' + module)
            # A helper has no window: what PyInstaller set for Qt and for its own
            # bootloader must not reach the programs it supervises.
            from .linux_bundle import child_environment
            clean = child_environment()
            os.environ.clear()
            os.environ.update(clean)
            sys.argv = [module, *sys.argv[3:]]
            runpy.run_module(module, run_name='__main__', alter_sys=True)
            raise SystemExit(0)
    smoke = '--smoke-test' in sys.argv
    connect = json.loads(sys.argv[sys.argv.index('--smoke-connect') + 1]) if smoke and '--smoke-connect' in sys.argv else None
    from .desktop_runtime import launcher_root
    portable_root = Path(sys.executable).parent
    if session is None and getattr(sys, 'frozen', False) and (io_path(portable_root) / 'portable.json').is_file() and not smoke:
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
    instance = None
    if session is None and not smoke:
        # A launcher already running for this installation, perhaps in the background, shows its window and
        # this start ends here: it must neither build a second session nor start a second ComfyUI.
        from PySide6.QtWidgets import QApplication
        from .windows_ux import taskbar_identity
        taskbar_identity()
        QApplication.instance() or QApplication([sys.argv[0]])
        instance = instance_name()
        if hand_to_running_instance(instance):
            return
    from .launcher_session import Session
    from .launcher_settings import Store
    if session is None:
        session = Session(smoke=smoke, store=Store(launcher_root(),
            documents=launcher_root() / 'smoke-documents' if smoke else None))
    app, engine, window, bridge = create_ui(session, instance=instance)
    if smoke:
        from PySide6.QtCore import QTimer
        destination = sys.argv[sys.argv.index('--smoke-test')+1]
        if connect is not None:
            QTimer.singleShot(0, lambda: smoke_connect(app, engine, window, bridge, destination, connect))
        else:
            QTimer.singleShot(0, lambda: smoke_test(app, engine, window, bridge, destination))
    try:
        app.exec()
    finally:
        bridge.shutdown()
        # Destroy the QML tree before its Python context properties.
        engine.deleteLater()
        app.processEvents()


if __name__ == '__main__':
    main()
