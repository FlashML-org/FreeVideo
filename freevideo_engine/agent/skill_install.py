"""Copy the bundled FreeVideo skill folder where Agents that support SKILL.md look for it."""
from pathlib import Path
import shutil

SOURCE = Path(__file__).resolve().parent / 'skill' / 'freevideo'
# Codex, Gemini CLI and Cursor read ~/.agents/skills; Claude Code reads ~/.claude/skills.
TARGETS = {'agents': ('.agents', 'skills'), 'claude': ('.claude', 'skills')}


def target_dir(target, home=None):
    return Path(home or Path.home()).joinpath(*TARGETS[target]) / 'freevideo'


def install(targets=('agents', 'claude'), home=None):
    """Replace the installed copy (only our own freevideo folder) and return the paths written."""
    written = []
    for target in targets:
        destination = target_dir(target, home)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.with_name('.freevideo-installing')
        shutil.rmtree(staging, ignore_errors=True)
        for source in SOURCE.rglob('*.txt'):
            # Stored as .txt so the update payload only carries file types every launcher accepts; agents read .md.
            copy = staging / source.relative_to(SOURCE).with_suffix('.md')
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, copy)
        if destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)
        written.append(str(destination))
    return written
