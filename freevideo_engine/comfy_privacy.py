"""Privacy defaults for the ComfyUI host launched in our managed environment."""
import json
import logging
import os
from pathlib import Path

from .comfy_logging import MANAGED_HOST_ENV

PERSIST = 'Comfy.Workflow.Persist'


def keep_no_workflow_drafts(user_root):
    """Turn off the ComfyUI page's workflow drafts once, unless the user already chose.

    With drafts on, the page copies every open workflow, prompts included, into the
    browser's storage, where it stays after the work is deleted. A value the user set,
    either way, is left alone; so is a settings file we cannot read as an object.
    Returns True when the setting was written.
    """
    path = Path(user_root) / 'default' / 'comfy.settings.json'
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        settings = {}
    except (OSError, ValueError):
        return False
    if not isinstance(settings, dict) or PERSIST in settings:
        return False
    settings[PERSIST] = False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.freevideo-tmp')
    temporary.write_text(json.dumps(settings, indent=4), encoding='utf-8')
    os.replace(temporary, path)
    return True


def configure_managed_privacy():
    """Applied only in the ComfyUI FreeVideo installed; a user's own ComfyUI keeps its settings."""
    if os.environ.get(MANAGED_HOST_ENV) != '1':
        return
    try:
        import folder_paths
        keep_no_workflow_drafts(folder_paths.get_user_directory())
    except Exception as error:  # The page still works; drafts keep their old setting.
        logging.warning('[FreeVideo] Workflow draft setting not applied: %s', error)
