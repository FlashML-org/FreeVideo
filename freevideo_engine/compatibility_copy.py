"""Startup wording shared by the native launchers."""
from .compatibility import RECOVERY_SUCCESSES


def notice_text(state, zh=False):
    if state.get('recovery_disabled'):
        return ('为了稳定，生成已自动放慢；可在设置里调整。' if zh else
                'Generation is slowed automatically for stability; you can change this in Settings.')
    required = state.get('recovery_successes_required') or RECOVERY_SUCCESSES
    if required == 1:
        return ('为了稳定，生成已自动放慢；下一次生成完成后会加快一些，也可在设置里调整。' if zh else
                'Generation is slowed automatically for stability; it speeds up after the next successful generation, or you can change this in Settings.')
    return (f'为了稳定，生成已自动放慢；连续完成 {required} 次后会加快一些，也可在设置里调整。' if zh else
            f'Generation is slowed automatically for stability; it speeds up after {required} successful generations in a row, or you can change this in Settings.')
