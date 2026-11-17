"""FreeVideo inference policies layered over the pinned VDN model."""
__version__ = "2026.9.16.41444"

# Release builds stamp one UTC version into both the GUI and deployed engine.
from pathlib import Path as _Path
_build_version = _Path(__file__).with_name('build-version.txt')
try:
    from .long_paths import io_path as _io_path
except ModuleNotFoundError as _error:
    # Version-stamp probes may copy only this initializer, without the engine.
    # A complete launcher always has long_paths; do not hide its import errors.
    if _error.name != __name__ + '.long_paths':
        raise
else:
    _build_version = _io_path(_build_version)
    from .long_paths import start_process as _start_process
    _start_process()
    from .triton_compat import own_temporary_environment as _temporary_environment
    _temporary_environment()
    from .library_path import isolate as _isolate_libraries
    _isolate_libraries()
if _build_version.is_file():
    __version__ = _build_version.read_text(encoding='utf-8').strip()
