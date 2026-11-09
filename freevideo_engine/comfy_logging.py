"""Logging policy for the ComfyUI host launched in our managed environment."""
import logging
import os

MANAGED_HOST_ENV = 'FREEVIDEO_MANAGED_COMFYUI'


class _OptionalTemplatesFilter(logging.Filter):
    def filter(self, record):
        return 'comfyui-workflow-templates is not installed.' not in record.getMessage()


_templates_filter = _OptionalTemplatesFilter()


def configure_managed_logging():
    """Hide upstream's reinstall advice for the template library we intentionally omit.

    Upstream uses logging.error on the root logger for both template lookup paths.
    Filtering there keeps this one record out of every handler, including diagnostics.
    User-owned ComfyUI instances keep all their logging behavior.
    """
    if os.environ.get(MANAGED_HOST_ENV) == '1':
        logging.getLogger().addFilter(_templates_filter)
