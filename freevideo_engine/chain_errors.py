"""Readable reasons for long-video (chain) failures: a code, an English and a Chinese sentence.

The original exception text is never one of the sentences; it goes to `detail` (routes) or stays in the
record's own `error`/`output_error` field, for the technical details and the report."""
import errno
import re

# code: (English, Chinese). Each sentence says what happened and what to do next.
ROUTE = {
    'busy': ('This long video is busy with another action. Wait until it finishes, then try again.',
             '这个长视频正在进行另一项操作。请等它完成后再试。'),
    'invalid': ('The long video request is not valid. Reload the page and try again; if it happens again, send a report.',
                '长视频请求无效。请刷新页面后重试；如果再次出现，请发送诊断报告。'),
    'disk_full': ('The disk is full, so the long video could not be saved. Free up disk space and try again.',
                  '磁盘空间不足，长视频没能保存。请清理磁盘空间后重试。'),
    'storage_unreadable': ('FreeVideo could not read or write the long video files. Check that the FreeVideo folder '
                           'can be opened and is not read-only, then try again.',
                           '长视频文件读写失败。请确认 FreeVideo 文件夹可以打开且不是只读，然后重试。'),
    'unknown_chain': ('This long video was not found. Reload the page to see the current list.',
                      '找不到这个长视频。请刷新页面查看当前列表。'),
    # The failure card shows it in its own words (web/error_panel.js REDO_CHANGED).
    'redo_request_changed': ('This redo was already submitted with the earlier settings. Check the queue first. '
                             'If you still want the current settings, choose "Redo from here" again.',
                             '这次重新生成已按之前的设置提交过。请先查看队列；'
                             '仍需要按现在的设置生成时，再点一次“从这段重新生成”。'),
}
HOST_UNAVAILABLE = {
    'after': ('Extend is not available in this version. Update FreeVideo and try again.',
              '当前版本不支持接着生成。请更新 FreeVideo 后重试。'),
    'other': ('Generating a new part before an existing part is not available in this version. Update FreeVideo and try again.',
              '当前版本不支持在已有片段前面生成新的一段。请更新 FreeVideo 后重试。'),
}
BLOCK = {
    'generation_failed': ('This part could not be generated. Continue from this part; if it fails again, send a report.',
                          '这一段没能生成。请从这段继续；如果再次失败，请发送诊断报告。'),
    'disk_full': ('The disk is full, so this part could not be saved. Free up disk space, then continue from this part.',
                  '磁盘空间不足，这一段没能保存。请清理磁盘空间，然后从这段继续。'),
    'storage_unreadable': ('FreeVideo could not read or write the long video files, so this part did not start. '
                           'Check that the FreeVideo folder can be opened and is not read-only, then continue from this part.',
                           '长视频文件读写失败，这一段没能开始。请确认 FreeVideo 文件夹可以打开且不是只读，然后从这段继续。'),
}
JOIN = {
    'join_failed': ('The parts could not be joined into one video. Try again; if it fails again, send a report.',
                    '各段没能接合成一个视频。请重试；如果再次失败，请发送诊断报告。'),
    'disk_full': ('The disk is full, so the joined video could not be saved. Free up disk space and try again.',
                  '磁盘空间不足，接合后的视频没能保存。请清理磁盘空间后重试。'),
}
# The same causes when only the text of an error is left, as from a prompt that failed in the host.
DISK_FULL_TEXT = re.compile(r'no space left on device|disk quota exceeded|winerror 112\b|errno 28\b|errno 122\b'
                            r'|not enough space on the disk|磁盘空间不足', re.I)


def _causes(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)


def disk_full(error):
    if isinstance(error, str):
        return bool(DISK_FULL_TEXT.search(error))
    return any(isinstance(item, OSError) and (item.errno in (errno.ENOSPC, getattr(errno, 'EDQUOT', errno.ENOSPC))
                                              or getattr(item, 'winerror', None) == 112) for item in _causes(error))


def storage_code(error):
    """'disk_full' or 'storage_unreadable' for an OSError (or one in its causes), else None."""
    if disk_full(error):
        return 'disk_full'
    if any(isinstance(item, OSError) for item in _causes(error)):
        return 'storage_unreadable'
    return None


def route_body(code, detail=None, **extra):
    english, chinese = ROUTE[code]
    body = dict(error=english, error_zh=chinese, code=code)
    if detail:
        body['detail'] = str(detail)[:2000]
    body.update(extra)
    return body


def host_unavailable_body(direction):
    english, chinese = HOST_UNAVAILABLE['after' if direction == 'after' else 'other']
    return dict(error=english, error_zh=chinese, code='host_unavailable')


def block_failure(error):
    """The fields a failed part keeps next to its original `error` text: the engine's own code and sentences
    when the error carries them, else disk_full, storage_unreadable or generation_failed."""
    if isinstance(error, dict):  # a host status: {'error': text, 'code'?, 'error_en'?, 'error_zh'?}
        code, text = error.get('code'), error.get('error') or ''
        english, chinese = error.get('error_en'), error.get('error_zh')
    else:
        code, text = getattr(error, 'code', None), str(error)
        english = getattr(error, 'message_en', None) or getattr(error, 'error_en', None)
        chinese = getattr(error, 'message_zh', None) or getattr(error, 'error_zh', None)
        if not english and isinstance(chinese, str):
            english = text  # an engine error with a Chinese sentence says its English one as its message
    if not (isinstance(code, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', code)):
        code = (storage_code(error) if not isinstance(error, dict) else None) or (
            'disk_full' if disk_full(text) else 'generation_failed')
    if not (isinstance(english, str) and english and isinstance(chinese, str) and chinese):
        english, chinese = BLOCK.get(code, BLOCK['generation_failed'])
    return dict(code=code, error_en=english, error_zh=chinese)


def join_failure(error):
    code = 'disk_full' if disk_full(error) or disk_full(str(error)) else 'join_failed'
    english, chinese = JOIN[code]
    return dict(output_error_code=code, output_error_en=english, output_error_zh=chinese)


BLOCK_FIELDS = ('code', 'error_en', 'error_zh')
OUTPUT_FIELDS = ('output_error_code', 'output_error_en', 'output_error_zh')
