"""Logging policy for the ComfyUI host launched in our managed environment."""
import logging
import os
import re

MANAGED_HOST_ENV = 'FREEVIDEO_MANAGED_COMFYUI'


# Upstream logs why a prompt failed validation with the value it was given: a picture or clip file name the user chose.
_VALUE = re.compile(r"^(  - (?:Value not in list|Custom validation failed for node)[^:]*): .*$", re.DOTALL)


class _ManagedLogFilter(logging.Filter):
    """Drops upstream's advice for the template library we omit; keeps the reason of a failed validation, not the file names."""
    def filter(self, record):
        message = record.getMessage()
        if 'comfyui-workflow-templates is not installed.' in message:
            return False
        found = _VALUE.match(message)
        if found:
            record.msg, record.args = found.group(1) + ': (value not logged)', None
        return True


_templates_filter = _ManagedLogFilter()


def configure_managed_logging():
    """Hide upstream's reinstall advice for the template library we intentionally omit.

    Upstream uses logging.error on the root logger for both template lookup paths.
    Filtering there keeps this one record out of every handler, including diagnostics.
    User-owned ComfyUI instances keep all their logging behavior.
    """
    if os.environ.get(MANAGED_HOST_ENV) == '1':
        logging.getLogger().addFilter(_templates_filter)
