"""PyInstaller entry point; model modules stay out of the GUI executable."""
import sys


def _failed():
    """The launcher could not load or stopped with an error: a short message instead of a raw traceback."""
    import traceback
    detail = traceback.format_exc()
    try:
        from freevideo_engine.startup_error import launcher_folder, report
        report(launcher_folder(), detail)
    except Exception:
        # The engine package itself could not load: keep the plain traceback where a person can see it.
        if sys.stderr is not None:
            sys.stderr.write(detail)
        elif sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, detail, 'FreeVideo startup error', 0x10)
    raise SystemExit(1)


if __name__ == '__main__':
    try:
        import freevideo_source_bootstrap
        freevideo_source_bootstrap.activate()
        from freevideo_engine.modern_launcher import main
        main()
    except Exception:
        _failed()
