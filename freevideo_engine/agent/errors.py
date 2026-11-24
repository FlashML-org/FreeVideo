"""Stable Agent error vocabulary. Backend exception text is never a public message.

Only implemented errors belong here. Future editor reasons (locked, overlap,
range, text, audio_full, MissingGlyphs) stay reserved until those routes exist.
"""
from contextvars import ContextVar
import math
import re
import uuid
from ..image_gen.failure import STAGES as IMAGE_STAGES
from ..image_gen.loras import ERRORS as LORA_ERRORS, MESSAGE_EN as LORA_MESSAGE_EN, MESSAGE_ZH as LORA_MESSAGE_ZH

REQUEST_ID = ContextVar('agent_request_id', default=None)
# The work session a token request belongs to (sessions.py); jobs and productions record it.
SESSION = ContextVar('agent_session', default=None)
_UNREAD_USAGE = object()
# The operation digest a confirmation approves: method, route, path values and body without ref.
CONFIRM = ContextVar('agent_confirm', default=None)
# code: HTTP status, retryable, public message, concrete recovery action
ERRORS = {
    'forbidden': (403, False, 'This caller cannot write this session event.', 'Only the user may stop or continue a session or answer a confirmation; steps are written by the server.'),
    'session_full': (409, False, 'This session has reached its event limit.', 'Create a new session with POST /freevideo/agent/v1/sessions.'),
    'lan_unavailable': (409, False, 'LAN settings require a launcher installation.', 'Complete installation in the FreeVideo launcher on this computer. 请在本机 FreeVideo 启动器中完成安装。'),
    'page_session_required': (403, False, 'A valid page session and CSRF header are required.', 'Reload the page served by this FreeVideo instance and repeat the action.'),
    'redirect_forbidden': (403, False, 'A credentialed request cannot follow a cross-origin redirect.', 'Connect directly to the intended FreeVideo instance using its verified HTTP or HTTPS endpoint.'),
    'method_not_allowed': (405, False, 'This method is not registered for the requested endpoint.', 'Read GET /freevideo/agent/v1/openapi.json and use the documented method.'),
    'unauthorized': (401, False, 'The bearer token is missing or incorrect.', 'Read the token file described by GET /freevideo/agent/v1/connect and send Authorization: Bearer <token>.'),
    'no_token': (0, False, 'The local bearer token is unavailable.', 'Start FreeVideo, then read GET /freevideo/agent/v1/connect.'),
    'unreachable': (0, True, 'The FreeVideo server could not be reached.', 'Start FreeVideo and retry the request.'),
    'invalid_json': (400, False, 'The request body must contain valid JSON; malformed JSON was received.', 'Send a JSON object with Content-Type: application/json.'),
    'invalid_request': (422, False, 'The request contains invalid fields.', 'Correct every field listed in details and resend the request.'),
    'invalid_media': (422, False, 'The supplied media is invalid or unreadable.', 'Upload a readable image, video or audio file with a supported extension using POST /freevideo/agent/v1/media.'),
    'invalid_edit': (422, False, 'The image edit recipe or document is invalid.', 'Read GET /freevideo/agent/v1/schema and correct the edit recipe before retrying.'),
    'invalid_screenplay': (400, False, 'The screenplay contains invalid fields.', 'Fix every listed problem, then call freevideo_drama_plan again.'),
    'too_large': (413, False, 'The request exceeds the permitted byte limit.', 'Reduce the upload to the limit_bytes value in details and retry.'),
    'unsupported_media_type': (415, False, 'Content-Type must be application/json or a supported media upload.', 'Send JSON with Content-Type: application/json; use multipart/form-data only for POST /media.'),
    'not_found': (404, False, 'The requested job, film or asset does not exist.', 'List /freevideo/agent/v1/jobs, /drama or /library and select an existing ID.'),
    'no_video': (409, False, 'The source job has no finished video.', 'GET /freevideo/agent/v1/jobs/{id} and wait for a succeeded job containing result.video.'),
    'not_ready': (503, True, 'The FreeVideo installation is not ready.', 'Open Settings → Install / repair and finish setup. If the installation token is unsafe or incomplete, restore a regular non-symlink agent/token owned by your account, or remove that token and restart FreeVideo.'),
    'page_required': (409, True, 'This needs the FreeVideo page open in a browser.', 'Ask the user to open FreeVideo in their browser, then retry.'),
    'busy': (409, True, 'The requested resource is currently in use.', 'Wait retry_after_seconds, read GET /freevideo/agent/v1/queue, then retry the request.'),
    'conflict': (409, False, 'The requested operation conflicts with the current state.', 'Read the current job with GET /freevideo/agent/v1/jobs/{id} before retrying.'),
    'dependency_failed': (409, False, 'A required job did not succeed.', 'Read the dependency jobs, resume failed generation, then submit the dependent job with a new ref.'),
    'cancelled': (409, False, 'The job was cancelled.', 'Resume the generation with POST /freevideo/agent/v1/jobs/{id}/resume if you want it to continue.'),
    'lost': (409, True, 'The backend no longer has this job result.', 'Read the job; resume generation or submit a new non-generation job with a new ref.'),
    'interrupted': (409, True, 'The job was interrupted; completed blocks are retained.', 'POST /freevideo/agent/v1/jobs/{id}/resume to continue generation.'),
    'generation_failed': (409, True, 'Generation failed; completed blocks are retained.', 'POST /freevideo/agent/v1/jobs/{id}/resume to retry unfinished blocks.'),
    'insufficient_memory': (409, False, 'The requested generation exceeds the available memory budget.', 'Use the numerical alternatives in details or close other GPU applications, then submit a new job with a new ref.'),
    'out_of_memory': (409, True, 'The backend ran out of memory.', 'Close other GPU applications, then POST /freevideo/agent/v1/jobs/{id}/resume using the existing job ID.'),
    'vlm_unavailable': (503, True, 'The local vision-language service is unavailable.', 'Open Settings → Install / repair and install the prompt model, then retry.'),
    'vlm_failed': (409, True, 'The local vision-language request failed.', 'Submit the question again with a new ref.'),
    'model_missing': (503, False, 'The required local model is not installed.', 'Open Settings → Install / repair and install the prompt model, then retry.'),
    'ram_space': (409, True, 'The local model has insufficient available system memory.', 'Close other memory-intensive applications and retry with a new ref.'),
    'context_length': (422, False, 'The question and images exceed the local model context limit.', 'Shorten the question or send fewer images, then submit with a new ref.'),
    'invalid_output': (409, True, 'The backend output is invalid or unavailable.', 'Restore the saved output, then POST /freevideo/agent/v1/jobs/{id}/resume; for a vision-language job, submit a new request.'),
    'vlm_invalid_json': (409, True, 'The local model returned an answer that is not valid JSON.', 'Submit the question again with format=json and a new ref.'),
    'timeout': (504, True, 'The backend did not finish within its time limit.', 'Read the job before retrying; resume generation or resubmit the question with a new ref.'),
    'image_size': (422, False, 'The image exceeds the local model limit of 50 megapixels.', 'Resize the image to at most 50 megapixels, upload it, and submit with a new ref.'),
    'unsupported_media': (422, False, 'The local model accepts images, not this media type.', 'Use POST /freevideo/agent/v1/media/frame to extract an image before asking the model.'),
    'internal_error': (500, True, 'An internal error prevented this operation from completing.', 'Read the job list and report request_id if the error persists.'),
    'local_only': (403, False, 'This endpoint does not allow requests from this peer address.', 'Run the client on the FreeVideo computer and connect using its loopback address.'),
    'origin_forbidden': (403, False, 'Origin must match this FreeVideo instance; a different origin was received.', 'Open the page served by this FreeVideo instance, or use a local command-line client without Origin.'),
    'continuation_unavailable': (409, False, 'This backend cannot continue video blocks.', 'Use POST /plan for a supported single block (unknown limit: seconds<=15), or enable continuation and resume.'),
    'not_resumable': (409, False, 'Only failed, interrupted or cancelled generation jobs can resume.', 'Read the job; wait for active jobs or submit a new job for a different request.'),
    'idempotency_conflict': (409, False, 'This idempotency key was already used for different request content.', 'Resend the original content with this key, or use a new ref for the changed request.'),
    'source_missing': (409, False, 'A completed source block is no longer available.', 'Restore the completed block before resuming, or submit a new job.'),
    'storage_unavailable': (503, True, 'The task receipt or media could not be stored.', 'Restore write access and free disk space, then retry the request.'),
    'invalid_range': (416, False, 'Range must select bytes inside this file; an invalid range was received.', 'Remove the Range header to download the complete file.'),
    'chain_unavailable': (503, True, 'The continuous generation backend is unavailable.', 'Start FreeVideo with the continuous generation backend enabled, then retry.'),
    'chain_missing': (409, True, 'The saved chain receipt is unavailable.', 'Restore the chain receipt and retry resume, or submit a new job.'),
    'join_failed': (409, True, 'The generated blocks could not be joined; completed blocks are retained.', 'POST /freevideo/agent/v1/jobs/{id}/resume to retry joining the completed blocks.'),
    'chain_too_long': (422, False, 'One continuous shot can have up to 8 parts. Make it shorter.', 'Submit a new job with a smaller seconds, or cut to a new shot with a new job without continue_from; continue_from counts the parts of the video it continues.'),
    'chain_short_parts': (422, False, 'With a part shorter than 10 seconds, one continuous shot can have up to 4 parts. Make every part at least 10 seconds long, or use fewer parts.', 'Submit a new job whose continuing parts are at least 10 seconds, with fewer parts, or with first_frame_reference true; continue_from counts the parts of the video it continues.'),
    'chain_rejected': (409, False, 'The continuous backend rejected this generation.', 'Read GET /freevideo/agent/v1/capabilities and submit a supported generation request.'),
    'invalid_wait': (422, False, 'wait must be a finite nonnegative number of seconds.', 'Use wait=0 or wait=25; larger valid values are clamped to 25 seconds.'),
    'invalid_resume': (422, False, 'resume accepts an empty object or an optional ref only.', 'Send {} or {"ref":"resume-1"}; submit a new job to change generation parameters.'),
    'invalid_library_id': (422, False, 'day and run must identify a saved library video.', 'GET /freevideo/agent/v1/library and use an existing YYYY-MM-DD/32-hex ID.'),
    'invalid_capabilities_query': (422, False, 'The capabilities query contains invalid size, task or material fields.', 'Use width=1344&height=768&task=t2va&adapters=0, or correct all fields in details.'),
    'unsupported_comfyui': (503, False, 'This ComfyUI version has no supported generation endpoint.', 'Open Settings → Install / repair to install a supported ComfyUI version.'),
}

# not_ready predates productions and also means installation-not-ready (503).
# Preserve that runtime contract while the pure production code keeps its 409.
PRODUCTION_STATUS = {'not_ready': 409}
ERRORS.update({
    'capabilities_computing': (503, True, 'This computer is still working out what it can generate.', 'Wait retry_after_seconds, then retry the request.'),
    'unknown_shot': (404, False, 'The shot does not exist.', 'Use a shot id from the production view.'),
    'unknown_take': (404, False, 'The take does not exist.', 'Use a take number from this shot in the production view.'),
    'take_not_ready': (409, False, 'The take is stale or has not succeeded.', 'Choose a succeeded, non-stale take, or reshoot this shot.'),
    'chosen_by_user': (409, False, 'This shot was chosen by the user.', 'Leave this shot unchanged; only the user may change it.'),
    'nothing_to_cancel': (409, False, 'There are no unfinished takes or joins to cancel.', 'Choose completed takes, reshoot a shot, or finish the production.'),
    'not_allowed': (409, False, 'Only the user may stop or continue this film.', 'Wait for the user to stop or continue the film.'),
    'already_ended': (409, False, 'This film has already ended.', 'Use an unfinished, uncancelled production.'),
    'needs_confirmation': (409, False, 'The user must approve this operation first.', 'Wait with freevideo_wait (no id) for the user\'s answer; once approved, repeat the same call unchanged.'),
    'declined_by_user': (409, False, 'The user declined this operation.', 'Do not repeat this call; change the plan or ask the user what they want instead.'),
    'stopped_by_user': (409, False, 'The user stopped this session.', 'Wait for the user to continue the session; do not retry or start a new one.'),
    'turn_abandoned': (409, False, 'The user gave up on this turn.', 'Stop working on it. Wait for the next message from the user; do not retry.'),
    'reshoot_limit': (409, False, 'This shot reached its generation limit.', 'Choose the best existing take, rewrite the shot, or ask the user.'),
    'production_limit': (409, False, 'This film reached its child job limit.', 'Choose existing takes or ask the user how to reduce the film.'),
    'managed_by_production': (409, False, 'This job belongs to a film and cannot be resumed on its own.', 'This take belongs to a film; reshoot the shot or wait for the user to continue.'),
    'submit_interrupted': (409, True, 'The server stopped before it confirmed whether this take was submitted.', 'Reshoot this shot.'),
})
ERRORS.update({
    'stale_revision': (409, False, 'The card changed since you read it.', 'Apply your change to error.card and send its revision.'),
    'changed_by_user': (409, False, 'The user changed this part of the card.', "Keep the user's change; do not change it back. Ask the user if it must change."),
    'card_in_use': (409, False, 'An unfinished film uses this card.', 'Ask the user before deleting this card.'),
    'already_generating': (409, False, 'Reference pictures for this card are already being made.', 'Wait for error.job to end, then get the card.'),
    'project_changed': (409, False, 'The project changed since you read it.', 'Apply your operations to error.project and send its revision.'),
    'clip_busy': (409, True, 'A part in this operation is still generating.', 'Wait for its job to end, get the project, then send the operations again.'),
    'clip_locked': (409, False, 'A part in this operation is on a track the user locked.', 'Leave the locked track unchanged; ask the user to unlock it if it must change.'),
    'audio_full': (409, False, 'There is no free sound track for this operation.', 'Move or remove sound in error.project at that place, then retry.'),
    'overlap': (409, False, 'The operation would overlap parts that cannot move.', 'Choose another frame or move the parts in the way first.'),
    'missing_glyphs': (422, False, 'Text in the project uses characters the bundled fonts cannot draw.', 'Change the text of the parts listed in details, then export the new revision.'),
    'image_changed': (409, False, 'The image changed since you read it.', 'Apply your operations to error.image and send its revision.'),
    'layer_locked': (409, False, 'An operation changes a layer the user locked.', 'Leave the locked layer unchanged; ask the user to unlock it if it must change.'),
    'export_failed': (409, True, 'The export did not finish.', 'Read the project, then submit the export again with a new ref.'),
    'no_speech_model': (409, False, 'No speech model is installed on this machine.',
                        'Do not retry. Write the subtitle lines yourself with the subtitles edit, or tell the user the model is not installed.'),
    'speech_language': (409, False, 'The speech model does not cover the language of this sound.',
                        'Do not retry; tell the user the language is not supported, or write the lines yourself.'),
    'listen_failed': (409, True, 'Listening to the sound did not finish.',
                      'Read the project, then submit the listening job again with a new ref; if it fails again, write the lines yourself.'),
    'music_download_failed': (409, True, 'The music model could not be downloaded.',
                              'Tell the user the music model needs about 20 GB of free disk and a network connection, then '
                              'submit the music job again with a new ref; if it fails again, write the score yourself.'),
    'music_failed': (409, True, 'Composing the music did not finish.',
                     'Read the project, then submit the music job again with a new ref; if it fails again, write the score yourself.'),
    'stems_download_failed': (409, True, 'The sound separation model could not be downloaded.',
                              'Tell the user the model needs a network connection, then submit the job again with a new ref.'),
    'stems_failed': (409, True, 'Splitting the sound did not finish.',
                     'Read the project, then submit the job again with a new ref; if it fails again, leave the sound as it is.'),
    'sound_failed': (409, True, 'The sound of the cut was not finished.',
                     'Read the project, then submit the job again with a new ref; if it fails again, leave the sound as it is.'),
    'voice_failed': (409, True, 'Changing the voice did not finish.',
                     'Read the project, then submit the job again with a new ref; if it fails again, leave the sound as it is.'),
    'voice_model_missing': (409, False, 'The dubbing model is not ready on this machine.',
                            'Tell the user to install the dubbing model first; do not retry.'),
    'no_speech': (409, False, 'No speech was found in the selection.',
                  'Do not retry; choose a stretch of sound with someone speaking.'),
    'shorten_unavailable': (409, False, 'Lines cannot be shortened here yet.',
                            'Write the shorter line yourself and send it with the other lines.'),
    'clip_source_unusable': (409, False, 'A clip\'s source is missing, or cannot be read as far as the clip uses it.',
                             'Read the project and check each clip\'s source: it must exist and play, and in + frames must stay '
                             'within its real length. Correct the project, then export the new revision.'),
})


# Static bilingual image reasons; backend exception strings never enter the API.
IMAGE_REASONS = {
    'small_memory': ('This Mac has less than 16 GB of memory and cannot generate pictures.',
                     '这台 Mac 的内存不足 16 GB，无法生成图片。'),
    'no_memory': ('Not enough GPU memory, even at the smallest size. Close other apps using the GPU, then try again.',
                  '显存不足，最小尺寸也无法生成。请关闭其他占用显卡的程序后重试。'),
    'busy_video': ('A video is generating. Try again when it finishes.', '视频正在生成，完成后再试。'),
    'pack_missing': ('Download "Image generation models" in the launcher first.',
                     '请先在启动器里下载“图像生成模型”。'),
    'model_unavailable': ('This computer does not support the chosen picture model. Use Qwen-Image 2.1.',
                          '这台电脑不支持所选的图片模型。请改用 Qwen-Image 2.1。'),
    'model_unreadable': ('The image generation model files could not be read. Repair them in the launcher and try again.',
                         '图像生成模型的文件读不出来。在启动器里修复后再试。'),
    'device_unavailable': ('The graphics card is not available right now. Try again later.',
                           '显卡暂时用不了，稍后再试。'),
    'worker_exit': ('Image generation stopped unexpectedly. Try again.', '图像生成意外停止了，请再试一次。'),
    'timeout': ('This image took too long and was stopped. Try again.', '这一张用时太长，已停止。请再试一次。'),
    'disk_full': ('Saving the image failed: the disk is full. Free some space, then generate again.',
                  '保存图片时出错：磁盘空间不够，空出空间后再生成。'),
    'generation_failed': ('Image generation ran into an error. Try again.', '图像生成出错了，请再试一次。'),
    'invalid_media': ('The reference image could not be read. Choose another one.',
                      '参考图读不出来，请换一张。'),
    'invalid_request': ('The image settings are invalid. Adjust them and try again.', '图片设置有误，请调整后重试。'),
    'invalid_prompt': ('The prompt is empty, or longer than 2,000 characters once the picture references are counted. Shorten it and try again.',
                       '提示词为空，或加上图片引用后超过 2000 字。请缩短提示词后重试。'),
    'invalid_dimensions': ('The image size is outside what the chosen model supports. Change the size and try again.',
                           '图片尺寸超出所选模型支持的范围。请调整尺寸后重试。'),
    'invalid_seed': ('The seed is not valid. Clear it or use a smaller whole number, then try again.',
                     '随机种子无效。请清空种子或改用较小的整数后重试。'),
    'too_many_images': ('There are more reference images than the chosen model supports. Remove some and try again.',
                        '参考图超过所选模型支持的数量。请减少参考图后重试。'),
    'output_unwritable': ('The folder that keeps the pictures cannot be written (freevideo-image in the ComfyUI input folder). Check that it exists, is writable and has no file of the same name, then try again.',
                          '无法写入保存图片的文件夹（ComfyUI 的 input 目录下的 freevideo-image）。请确认该目录存在、可写，且没有同名文件，然后重试。'),
    'busy': ('Another picture is being generated. Try again when it finishes.', '已有一张图在生成，完成后再试。'),
    'cancelled': ('The picture was cancelled.', '已取消这张图片。'),
}


def image_failure(code, report_id=None):
    """Only fixed sentences and a validated opaque report ID cross the image boundary."""
    code = code if isinstance(code, str) else 'generation_failed'
    en, zh = IMAGE_REASONS.get(code, IMAGE_REASONS['invalid_request'] if code.startswith('invalid_')
                             or code == 'too_many_images' else IMAGE_REASONS['generation_failed'])
    valid = isinstance(report_id, str) and re.fullmatch(r'[0-9a-f]{32}', report_id)
    return dict(reason=dict(en=en, zh=zh), report_id=report_id if valid and code not in LORA_ERRORS and code not in (
        'busy', 'cancelled', 'invalid_request', 'invalid_media', 'invalid_prompt', 'invalid_dimensions',
        'invalid_seed', 'too_many_images', 'disk_full', 'output_unwritable') else None)


# Recovery is a function of the committed task and phase, not just an error code.
IMAGE_ERRORS = {
    **LORA_MESSAGE_EN,
    'small_memory': IMAGE_REASONS['small_memory'][0],
    'invalid_request': 'The picture request contains invalid fields.',
    'invalid_prompt': 'The picture prompt must contain text and be at most 2000 characters.',
    'invalid_dimensions': 'Picture width and height must be integers from 256 to 2048.',
    'invalid_seed': 'The picture seed must be an integer from -9223372036854775808 to 18446744073709551615.',
    'invalid_media': 'A reference picture must be a regular file inside the input directory without symlinks.',
    'too_many_images': 'Use at most four reference pictures.',
    'busy': 'Another picture is being generated; this picture is waiting for it.',
    'busy_video': 'A video is being generated; this picture is waiting for it.',
    'pack_missing': 'The image model is not installed on this computer; tell the user to install it in FreeVideo first and do not retry.',
    'model_unavailable': 'This computer cannot run the chosen picture model (H3 Image on a Mac); submit again with model qwen-image-2.1-turbo or without a model.',
    'unknown_job': 'The image service no longer has this picture job.',
    'closed': 'The image service has shut down.',
    'image_unavailable': 'The image service is not loaded on this computer.',
    'no_memory': 'There is not enough GPU or system memory for this picture.',
    'model_unreadable': IMAGE_REASONS['model_unreadable'][0],
    'device_unavailable': IMAGE_REASONS['device_unavailable'][0],
    'timeout': IMAGE_REASONS['timeout'][0],
    'generation_failed': IMAGE_REASONS['generation_failed'][0],
    'worker_exit': 'The image worker stopped before finishing this picture.',
    'disk_full': IMAGE_REASONS['disk_full'][0],
    'output_unwritable': 'The folder that keeps the pictures cannot be written; tell the user to check that the freevideo-image folder in the input directory exists, is writable and has no file of the same name, then submit again with a new ref.',
    'cancelled': 'The picture job was cancelled.',
}
IMAGE_REASONS.update({code: (LORA_MESSAGE_EN[code], LORA_MESSAGE_ZH[code]) for code in LORA_ERRORS})
for _code, _message in IMAGE_ERRORS.items():
    if _code not in ERRORS:
        ERRORS[_code] = (503 if _code in ('pack_missing', 'closed', 'image_unavailable') else 422,
                        _code in ('busy_video', 'unknown_job', 'closed', 'image_unavailable', 'worker_exit'),
                        _message, 'Read the picture job before submitting a corrected request with a new ref.')
ERRORS['model_unavailable'] = (422, False, IMAGE_ERRORS['model_unavailable'],
                        'Submit the picture again with model qwen-image-2.1-turbo or without a model, using a new ref.')
ERRORS['pack_missing'] = (503, False, IMAGE_ERRORS['pack_missing'],
                        'Tell the user to install the image model in FreeVideo first; do not retry.')

RECOVERY = {
    'generate': 'POST /freevideo/agent/v1/jobs/{id}/resume to retry unfinished generation blocks.',
    'vlm': 'Submit the question again with a new ref.',
    'enhance': 'Submit the prompt enhancement again with a new ref.',
    'image': 'Submit the picture request again with a new ref.',
    'assemble': 'Submit the clip assembly again with a new ref.',
    'refine': 'Submit the second-pass generation again with a new ref.',
    'submission_unknown': 'Read the job list, then retry the original submission with the same ref.',
}
REPAIR = {
    'out_of_memory': 'Close other GPU applications first. ',
    'insufficient_memory': 'Close other GPU applications, or use the numerical alternatives in details and submit with a new ref. ',
    'storage_unavailable': 'Restore write access and free disk space first. ',
    'source_missing': 'Restore the missing source first. ',
    'chain_missing': 'Restore the saved chain receipt first. ',
    'not_ready': 'Open Settings → Install / repair first. ',
    'model_missing': 'Install the required prompt model using Settings → Install / repair first. ',
    'vlm_unavailable': 'Install or start the prompt model using Settings → Install / repair first. ',
}


# A conversation that is not there: the user may delete one while the agent still works or waits in it.
SESSION_GONE = ('This conversation does not exist; the user may have deleted it.',
                'Do not repeat elsewhere what you were saying or doing for it. Call the wait again without a session '
                '(GET /freevideo/agent/v1/inbox?wait=50; MCP: freevideo_wait without id) and carry on in the session '
                'it names.')


def recovery(code, *, kind=None, stage='request', resumable=True):
    if kind == 'session' and code == 'not_found':
        return SESSION_GONE[1]
    if kind == 'image' and code in ('pack_missing', 'model_unavailable'):
        return ERRORS[code][3]
    if kind == 'image' and code == 'disk_full':
        return 'Tell the user to free disk space, then submit the picture again with a new ref.'
    if kind == 'music' and code == 'out_of_memory':
        return 'Close other GPU applications, then submit the music job again with a new ref.'
    if kind in ('stems', 'sound', 'finish_sound', 'voice', 'voice_lines', 'voice_say') and code == 'out_of_memory':
        return 'Close other GPU applications, then submit the job again with a new ref.'
    if kind == 'image' and code == 'no_memory':
        return 'Lower the picture dimensions or use fewer reference pictures, then submit with a new ref.'
    if kind in RECOVERY and stage == 'terminal':
        return REPAIR.get(code, '') + RECOVERY['refine' if kind == 'generate' and not resumable else kind]
    if stage == 'submission_unknown' and code in ('internal_error', 'storage_unavailable', 'unreachable'):
        return REPAIR.get(code, '') + RECOVERY['submission_unknown']
    action = ERRORS[code][3]
    if '/resume' in action:
        return 'Read the job status; submit a corrected new request with a new ref if no resumable generation exists.'
    return action


def request_id():
    return REQUEST_ID.get() or 'r-' + uuid.uuid4().hex


def safe_details(value):
    """Only schema-created diagnostics and numerical backend alternatives cross the boundary."""
    if isinstance(value, list):
        return [safe_details(v) for v in value]
    if isinstance(value, dict):
        return {k: (v if k == 'failure_kind' and v in ('allocation', 'gpu_oom', 'ram_pressure', 'invalid_inputs')
                    or k == 'confirm' and isinstance(v, str) and re.fullmatch(r'cf[0-9a-f]{10}', v)
                    else safe_details(v)) for k, v in value.items() if k in {
            'field', 'path', 'allowed', 'got', 'limit_bytes', 'block', 'total',
            'width', 'height', 'frames', 'seconds', 'failure_kind', 'alternatives', 'owner', 'agent', 'ui', 'count',
            'confirm'}}
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else str(value)
    # Backend strings can contain paths, tokens and prompts. Validators mark
    # their own messages separately; raw backend strings are never reflected.
    return '[redacted]'


# Picture-only codes: the people-facing sentence is the picture card's own (IMAGE_REASONS), word for word.
IMAGE_ONLY = ('small_memory', 'busy_video', 'model_unreadable', 'device_unavailable')
# Program-facing codes: only an Agent or a command-line client meets them, never a person as a task's failure.
PROTOCOL_ONLY = ('forbidden', 'redirect_forbidden', 'method_not_allowed', 'unauthorized', 'no_token', 'invalid_json',
                 'unsupported_media_type', 'idempotency_conflict', 'invalid_range', 'invalid_wait', 'invalid_resume',
                 'invalid_library_id', 'invalid_capabilities_query')


# One public sentence shared by conversation cards, HTTP and MCP. Backend text
# is classified, never echoed: it can contain prompts, filenames or credentials.
FAILURE_REASONS_ZH = {
    **LORA_MESSAGE_ZH,
    'too_large': '这条消息太长，请缩短或分成两条发送',
    'lan_unavailable': '手机连接需要用启动器安装的 FreeVideo',
    'capabilities_computing': '正在计算这台电脑的生成能力，稍后再试',
    'out_of_memory': '生成时显存或内存不足',
    'insufficient_memory': '可用显存或内存不足，无法生成这段内容',
    'no_memory': '可用显存或内存不足，无法生成这张图片',
    'ram_space': '加载模型时系统内存不足',
    'model_missing': '需要的模型还没有安装好',
    'pack_missing': '图片模型还没有安装好',
    'model_unavailable': '这台电脑不支持所选的图片模型',
    'not_ready': '生成所需的环境还没有安装好',
    'cancelled': '生成已被停止',
    'stopped_by_user': '生成已被停止',
    'interrupted': '生成中途被打断',
    'worker_exit': '图片生成中途被打断',
    'closed': '图片生成服务已经关闭',
    'image_unavailable': '图片生成服务还没有启动',
    'vlm_unavailable': '读取画面的模型服务还没有启动',
    'vlm_failed': '没能读出画面',
    'vlm_invalid_json': '回复读不出来',
    'context_length': '描述和参考图太长',
    'dependency_failed': '前面依赖的任务没有完成',
    'lost': '生成服务没有保留这次任务的结果',
    'unknown_job': '图片生成服务找不到这次任务',
    'not_found': '找不到这次任务或素材',
    'source_missing': '接续所需的上一段视频已经不在',
    'chain_missing': '接续所需的生成记录已经不在',
    'not_resumable': '此任务无法继续生成',
    'chain_unavailable': '分段生成服务还没有启动',
    'continuation_unavailable': '当前生成方式不支持接续视频',
    'join_failed': '片段已生成，但拼接成片时出错',
    'invalid_output': '生成的结果无法读取或保存',
    'storage_unavailable': '任务记录或素材无法写入磁盘',
    'disk_full': '磁盘空间不够，图片没能保存',
    'output_unwritable': '保存图片的文件夹无法写入',
    'submit_interrupted': '提交生成时服务中断，尚未确认任务是否开始',
    'timeout': '生成等了太久没有结果',
    'invalid_media': '参考素材无法读取',
    'clip_unreadable': '生成的角色参考视频无法读取',
    'invalid_request': '生成参数不符合支持的范围',
    'invalid_drama': '短剧设置有误',
    'reshoot_limit': '这个镜头已达到可生成的次数上限',
    'production_limit': '这部短剧已达到可生成的任务数量上限',
    'export_failed': '导出成片时出错',
    'invalid_prompt': '图片描述为空或超过了支持的长度',
    'invalid_dimensions': '图片尺寸超出了支持的范围',
    'too_many_images': '参考图片数量超过了支持的范围',
    'chain_too_long': '接续后的视频超过了支持的段数',
    'chain_short_parts': '有短于 10 秒的段时，接续后的视频超过了支持的段数',
    'chain_rejected': '生成服务无法使用这组参数',
    'generation_failed': '生成过程中出现了意外错误',
    'internal_error': '处理过程中出现了意外错误',
    'unreachable': 'FreeVideo 服务没有响应',
    'session_full': '这个对话的消息已达到上限',
    'page_session_required': '页面会话已失效',
    'origin_forbidden': '这次操作来自其他网址，已被拒绝',
    'local_only': '这项操作只能在运行 FreeVideo 的电脑上进行',
    'page_required': '这项操作需要在浏览器中打开 FreeVideo 页面',
    'unsupported_comfyui': '已安装的生成组件版本不受支持',
    'busy': '另一项任务正在占用所需的资源',
    'conflict': '这项操作与任务的当前状态冲突',
    'no_video': '来源任务还没有生成完成的视频',
    'invalid_edit': '图片编辑的设置有误',
    'invalid_screenplay': '剧本中有不符合要求的内容',
    'invalid_seed': '图片的随机种子超出了支持的范围',
    'image_size': '图片超过了 5000 万像素的上限',
    'unsupported_media': '读取画面的模型只能读取图片',
    'unknown_shot': '找不到这个镜头',
    'unknown_take': '这个镜头中找不到指定的那一条',
    'take_not_ready': '这一条已过期或没有生成成功',
    'chosen_by_user': '这个镜头已手动选定，不会自动更改',
    'nothing_to_cancel': '没有可以取消的未完成任务',
    'not_allowed': '这部短剧只能在页面上手动停止或继续',
    'already_ended': '这部短剧已经结束',
    'needs_confirmation': '这项操作需要先确认',
    'declined_by_user': '这项操作已被拒绝',
    'turn_abandoned': '这次请求已被放弃',
    'managed_by_production': '这一条属于一部短剧，不能单独继续生成',
    'stale_revision': '卡片在读取后已有改动',
    'changed_by_user': '这部分内容已被手动修改',
    'card_in_use': '有未完成的短剧正在使用这张卡片',
    'already_generating': '这张卡片的参考图正在生成',
    'project_changed': '项目在读取后已有改动',
    'image_changed': '图片在读取后已有改动',
    'clip_busy': '这次操作涉及的片段还在生成',
    'clip_locked': '这次操作涉及的片段在已锁定的轨道上',
    'layer_locked': '这次操作要修改已锁定的图层',
    'audio_full': '这里没有空闲的声音轨',
    'overlap': '这次操作会与不能移动的片段重叠',
    'missing_glyphs': '项目中的文字含有内置字体无法显示的字符',
    'clip_source_unusable': '有片段的源文件缺失，或在片段使用的范围内无法读取',
    'shorten_unavailable': '这里暂时不能自动缩短台词',
    'no_speech_model': '这台电脑上没有安装语音识别模型',
    'speech_language': '语音识别模型不支持这段声音的语言',
    'listen_failed': '识别台词时中途出错',
    'music_download_failed': '配乐模型未能下载',
    'music_failed': '生成配乐时中途出错',
    'stems_download_failed': '声音分离模型未能下载',
    'stems_failed': '分离声音时中途出错',
    'sound_failed': '处理成片声音时中途出错',
    'voice_failed': '更换人声时中途出错',
    'voice_model_missing': '配音模型还没有安装好',
    'no_speech': '所选范围内没有找到说话声',
    # Image-only codes keep the sentences the picture card already shows.
    **{code: IMAGE_REASONS[code][1] for code in IMAGE_ONLY},
}
FAILURE_REASONS_EN = {
    **LORA_MESSAGE_EN,
    'too_large': 'This message is too long; shorten it or send it in two parts',
    'lan_unavailable': 'Phone access requires FreeVideo installed with the launcher',
    'capabilities_computing': 'Working out what this computer can generate; try again shortly',
    'out_of_memory': 'Graphics or system memory ran out during generation',
    'insufficient_memory': 'There is not enough free graphics or system memory for this generation',
    'no_memory': 'There is not enough free graphics or system memory for this picture',
    'ram_space': 'System memory ran out while loading the model',
    'model_missing': 'A required model is not installed yet',
    'pack_missing': 'The image model is not installed yet',
    'model_unavailable': 'This computer does not support the chosen picture model',
    'not_ready': 'The generation environment is not installed yet',
    'cancelled': 'Generation was stopped',
    'stopped_by_user': 'Generation was stopped',
    'interrupted': 'Generation was interrupted',
    'worker_exit': 'Picture generation was interrupted',
    'closed': 'The picture generation service has closed',
    'image_unavailable': 'The picture generation service has not started yet',
    'vlm_unavailable': 'The model that reads pictures has not started yet',
    'vlm_failed': 'The picture could not be read',
    'vlm_invalid_json': 'The reply could not be read',
    'context_length': 'The description and reference pictures are too long',
    'dependency_failed': 'A task this one depends on did not finish',
    'lost': 'The generation service no longer has the result of this task',
    'unknown_job': 'The picture generation service cannot find this task',
    'not_found': 'This task or media file cannot be found',
    'source_missing': 'The previous video needed to continue is no longer available',
    'chain_missing': 'The generation record needed to continue is no longer available',
    'not_resumable': 'This task cannot be continued',
    'chain_unavailable': 'The part-by-part generation service has not started yet',
    'continuation_unavailable': 'The current generation mode cannot continue a video',
    'join_failed': 'The parts were generated, but joining them into one video failed',
    'invalid_output': 'The result could not be read or saved',
    'storage_unavailable': 'The task record or media could not be written to disk',
    'disk_full': 'Saving the image failed: the disk is full',
    'output_unwritable': 'The folder that keeps the pictures cannot be written',
    'submit_interrupted': 'The service stopped during submission, so it is not confirmed whether the task started',
    'timeout': 'Generation took too long without a result',
    'invalid_media': 'The reference media could not be read',
    'clip_unreadable': 'The generated character reference video could not be read',
    'invalid_request': 'The generation settings are outside the supported range',
    'invalid_drama': 'The short film settings are invalid',
    'reshoot_limit': 'This shot has reached its generation limit',
    'production_limit': 'This short film has reached its task limit',
    'export_failed': 'Exporting the video failed',
    'invalid_prompt': 'The picture description is empty or too long',
    'invalid_dimensions': 'The picture size is outside the supported range',
    'too_many_images': 'There are more reference pictures than supported',
    'chain_too_long': 'The continued video has more parts than supported',
    'chain_short_parts': 'With a part shorter than 10 seconds, the continued video has more parts than supported',
    'chain_rejected': 'The generation service cannot use these settings',
    'generation_failed': 'An unexpected error stopped generation',
    'internal_error': 'An unexpected error stopped this task',
    'unreachable': 'The FreeVideo service did not respond',
    'session_full': 'This chat has reached its message limit',
    'page_session_required': 'The page session is no longer valid',
    'origin_forbidden': 'This action came from a different web address and was refused',
    'local_only': 'This action is available only on the computer running FreeVideo',
    'page_required': 'This action needs the FreeVideo page open in a browser',
    'unsupported_comfyui': 'The installed generation component version is not supported',
    'busy': 'Another task is using the resources this one needs',
    'conflict': 'This action conflicts with the current state of the task',
    'no_video': 'The source task has no finished video yet',
    'invalid_edit': 'The image edit settings are invalid',
    'invalid_screenplay': 'The screenplay contains invalid content',
    'invalid_seed': 'The picture seed is outside the supported range',
    'image_size': 'The image exceeds the 50-megapixel limit',
    'unsupported_media': 'The model that reads pictures can read only images',
    'unknown_shot': 'This shot cannot be found',
    'unknown_take': 'The chosen take cannot be found in this shot',
    'take_not_ready': 'This take is out of date or did not succeed',
    'chosen_by_user': 'This shot was chosen by hand and is not changed automatically',
    'nothing_to_cancel': 'There is no unfinished task to cancel',
    'not_allowed': 'This short film can be stopped or continued only by hand on the page',
    'already_ended': 'This short film has already ended',
    'needs_confirmation': 'This action needs approval first',
    'declined_by_user': 'This action was declined',
    'turn_abandoned': 'This request was abandoned',
    'managed_by_production': 'This take belongs to a short film and cannot be continued on its own',
    'stale_revision': 'The card has changed since it was read',
    'changed_by_user': 'This part was changed by hand',
    'card_in_use': 'An unfinished short film uses this card',
    'already_generating': 'Reference pictures for this card are already being made',
    'project_changed': 'The project has changed since it was read',
    'image_changed': 'The image has changed since it was read',
    'clip_busy': 'A clip in this action is still generating',
    'clip_locked': 'A clip in this action is on a locked track',
    'layer_locked': 'This action changes a locked layer',
    'audio_full': 'There is no free sound track here',
    'overlap': 'This action would overlap clips that cannot move',
    'missing_glyphs': 'Text in the project contains characters the built-in fonts cannot display',
    'clip_source_unusable': 'A clip\'s source file is missing, or cannot be read over the part the clip uses',
    'shorten_unavailable': 'Lines cannot be shortened automatically here yet',
    'no_speech_model': 'No speech recognition model is installed on this computer',
    'speech_language': 'The speech recognition model does not support the language of this sound',
    'listen_failed': 'An error stopped recognizing the lines',
    'music_download_failed': 'The music model could not be downloaded',
    'music_failed': 'An error stopped composing the music',
    'stems_download_failed': 'The sound separation model could not be downloaded',
    'stems_failed': 'An error stopped separating the sound',
    'sound_failed': 'An error stopped finishing the sound of the cut',
    'voice_failed': 'An error stopped changing the voice',
    'voice_model_missing': 'The dubbing model is not installed yet',
    'no_speech': 'No speech was found in the selection',
    **{code: IMAGE_REASONS[code][0] for code in IMAGE_ONLY},
}
# What a person can do next, for the reasons above whose remedy is not just running the task again. Shown after the
# reason only while the reason itself is the code's sentence (a more specific message keeps its own meaning).
FAILURE_NEXT_ZH = {
    'generation_failed': '再次出现时，请点击“提交问题”',
    'internal_error': '再次出现时，请点击“提交问题”',
    'unreachable': '确认 FreeVideo 已经启动后再试',
    'session_full': '点击“新对话”后继续',
    'page_session_required': '刷新页面后再操作一次',
    'origin_forbidden': '在 FreeVideo 提供的页面中操作',
    'unsupported_comfyui': '在启动器或设置中修复安装',
    'busy': '等该任务完成后再试',
    'no_video': '等来源视频生成完成后再试',
    'image_size': '缩小图片后再试',
    'managed_by_production': '重拍这个镜头，或继续这部短剧',
    'already_generating': '等参考图生成完成后再试',
    'clip_busy': '等片段生成完成后再试',
    'clip_locked': '解锁该轨道后再试',
    'layer_locked': '解锁该图层后再试',
    'audio_full': '先挪开或删除这里的一段声音',
    'overlap': '换一个位置，或先挪开挡住的片段',
    'missing_glyphs': '修改这些文字后再导出',
    'clip_source_unusable': '找回或替换源文件后再导出',
    'shorten_unavailable': '手动改写较短的台词',
    'no_speech_model': '请手动填写字幕',
    'music_download_failed': '配乐模型需要约 20 GB 磁盘空间和网络连接，确认后再试',
    'stems_download_failed': '检查网络连接后再试',
    'voice_model_missing': '先安装配音模型',
    'no_speech': '选择有人说话的一段声音',
    'invalid_prompt': '缩短图片描述后再试',
    'invalid_dimensions': '调整图片尺寸后再试',
    'invalid_seed': '清空种子或改用较小的整数后再试',
    'too_many_images': '减少参考图后再试',
    'output_unwritable': '确认输入目录下的 freevideo-image 文件夹存在、可写且没有同名文件后再试',
    'invalid_lora_strength': '把强度调到 -2 到 2 之间后再试',
    'lan_unavailable': '先用启动器安装 FreeVideo，再打开“手机连接”',
    'turn_abandoned': '需要时重新提交',
    'insufficient_memory': '关闭其他占用显卡的程序，或缩小尺寸、缩短时长后再试',
    'no_memory': '关闭其他占用显卡的程序，或缩小图片尺寸后再试',
    'ram_space': '关闭其他占用内存的程序后再试',
    'model_missing': '在启动器或设置中安装所需模型后再试',
    'pack_missing': '在启动器或设置中安装图片模型后再试',
    'model_unavailable': '换一个图片模型后再试',
    'not_ready': '在启动器或设置中完成安装或修复后再试',
    'cancelled': '需要时重新提交',
    'stopped_by_user': '需要时重新提交',
    'interrupted': '再试一次',
    'worker_exit': '再试一次；再次出现时，请点击“提交问题”',
    'closed': '在启动器里重新启动 FreeVideo 后再试',
    'image_unavailable': '稍等片刻后再试；仍不行时，在启动器里重新启动 FreeVideo',
    'vlm_unavailable': '稍等片刻后再试；仍不行时，在启动器里重新启动 FreeVideo',
    'vlm_failed': '换一张更清晰的图片，或再试一次',
    'vlm_invalid_json': '再试一次',
    'context_length': '缩短描述或减少参考图后再试',
    'dependency_failed': '先处理前面失败的任务，再重新提交',
    'lost': '重新提交一次',
    'unknown_job': '刷新页面后查看任务列表，必要时重新提交',
    'not_found': '刷新页面后确认它还在，再重试',
    'source_missing': '找回或重新生成上一段视频后再接续',
    'chain_missing': '改为从完整视频重新接续',
    'not_resumable': '重新提交一个新任务',
    'chain_unavailable': '在启动器里重新启动 FreeVideo 后再试',
    'continuation_unavailable': '换一种生成方式，或重新生成整段视频',
    'join_failed': '检查磁盘空间后重新导出成片',
    'invalid_output': '检查磁盘空间后再试',
    'storage_unavailable': '检查磁盘空间和文件夹写入权限后再试',
    'disk_full': '清理磁盘空间后再试',
    'submit_interrupted': '查看任务列表确认是否已开始，没有再重新提交',
    'timeout': '再试一次；再次出现时，请缩短时长或降低尺寸',
    'invalid_media': '换一个能正常播放的文件后再试',
    'clip_unreadable': '重新生成这个角色参考视频后再试',
    'invalid_request': '把设置改到可用范围内后再试',
    'invalid_drama': '检查短剧设置后再试',
    'reshoot_limit': '改为选用已有的镜头，或新建一个镜头',
    'production_limit': '继续前，先完成或删除不需要的任务',
    'export_failed': '检查磁盘空间后重新导出',
    'chain_too_long': '缩短接续的内容，或减少段数后再试',
    'chain_short_parts': '把短于 10 秒的段合并或重新生成后再试',
    'chain_rejected': '更换尺寸或时长后再试',
    'local_only': '在运行 FreeVideo 的电脑上操作',
    'page_required': '在浏览器中打开 FreeVideo 页面后再操作',
    'conflict': '等当前任务结束，或刷新页面后再试',
    'invalid_edit': '检查编辑设置后再试',
    'invalid_screenplay': '按提示修改剧本后再试',
    'unsupported_media': '换一张图片后再试',
    'unknown_shot': '刷新页面后选择现有的镜头',
    'unknown_take': '刷新页面后选择这个镜头里现有的一条',
    'take_not_ready': '重新生成这一条，或选另一条',
    'chosen_by_user': '要更换时，请在页面上手动改选',
    'nothing_to_cancel': '无需操作，需要时再提交新任务',
    'not_allowed': '在页面上手动停止或继续这部短剧',
    'already_ended': '需要时新建一部短剧',
    'needs_confirmation': '确认后再执行这项操作',
    'declined_by_user': '需要时重新提出这项操作',
    'stale_revision': '刷新页面查看最新内容后再改',
    'changed_by_user': '按手动修改后的内容继续，或刷新页面后重新操作',
    'card_in_use': '等短剧结束，或先停止它后再改这张卡片',
    'project_changed': '刷新页面查看最新内容后再改',
    'image_changed': '刷新页面查看最新内容后再改',
    'speech_language': '换一段受支持语言的声音，或手动填写字幕',
    'listen_failed': '再试一次，或手动填写字幕',
    'music_failed': '再试一次',
    'stems_failed': '再试一次',
    'sound_failed': '再试一次',
    'voice_failed': '再试一次',
    'small_memory': '在内存 16 GB 及以上的 Mac 上使用图片生成',
}
FAILURE_NEXT_EN = {
    'generation_failed': 'If it happens again, use "Report a problem"',
    'internal_error': 'If it happens again, use "Report a problem"',
    'unreachable': 'Make sure FreeVideo is running, then try again',
    'session_full': 'Click "New chat" to continue',
    'page_session_required': 'Reload the page and repeat the action',
    'origin_forbidden': 'Use the page served by FreeVideo',
    'unsupported_comfyui': 'Repair the installation in the launcher or in Settings',
    'busy': 'Try again when that task finishes',
    'no_video': 'Try again after the source video finishes',
    'image_size': 'Make the image smaller, then try again',
    'managed_by_production': 'Reshoot this shot, or continue the short film',
    'already_generating': 'Try again when the reference pictures are finished',
    'clip_busy': 'Try again when the clip finishes generating',
    'clip_locked': 'Unlock the track, then try again',
    'layer_locked': 'Unlock the layer, then try again',
    'audio_full': 'Move or remove a sound clip here first',
    'overlap': 'Choose another position, or move the clips in the way first',
    'missing_glyphs': 'Change that text, then export again',
    'clip_source_unusable': 'Restore or replace the source file, then export again',
    'shorten_unavailable': 'Write a shorter line by hand',
    'no_speech_model': 'Write the subtitles by hand',
    'music_download_failed': 'The music model needs about 20 GB of free disk space and a network connection; check both, then try again',
    'stems_download_failed': 'Check the network connection, then try again',
    'voice_model_missing': 'Install the dubbing model first',
    'no_speech': 'Choose a stretch of sound with someone speaking',
    'invalid_prompt': 'Shorten the description, then try again',
    'invalid_dimensions': 'Change the picture size, then try again',
    'invalid_seed': 'Clear the seed or use a smaller whole number, then try again',
    'too_many_images': 'Remove some reference pictures, then try again',
    'output_unwritable': 'Check that the freevideo-image folder in the input directory exists, is writable and has no file of the same name, then try again',
    'invalid_lora_strength': 'Set the strength between -2 and 2, then try again',
    'lan_unavailable': 'Install FreeVideo with the launcher first, then turn on Phone access',
    'turn_abandoned': 'Submit it again when needed',
    'insufficient_memory': 'Close other programs that use the graphics card, or use a smaller size or a shorter length, then try again',
    'no_memory': 'Close other programs that use the graphics card, or use a smaller picture size, then try again',
    'ram_space': 'Close other programs that use memory, then try again',
    'model_missing': 'Install the required model in the launcher or in Settings, then try again',
    'pack_missing': 'Install the picture model in the launcher or in Settings, then try again',
    'model_unavailable': 'Choose another picture model, then try again',
    'not_ready': 'Finish the installation or repair in the launcher or in Settings, then try again',
    'cancelled': 'Submit it again when needed',
    'stopped_by_user': 'Submit it again when needed',
    'interrupted': 'Try again',
    'worker_exit': 'Try again; if it happens again, use "Report a problem"',
    'closed': 'Restart FreeVideo from the launcher, then try again',
    'image_unavailable': 'Wait a moment and try again; if it still fails, restart FreeVideo from the launcher',
    'vlm_unavailable': 'Wait a moment and try again; if it still fails, restart FreeVideo from the launcher',
    'vlm_failed': 'Try a clearer picture, or try again',
    'vlm_invalid_json': 'Try again',
    'context_length': 'Shorten the description or use fewer reference pictures, then try again',
    'dependency_failed': 'Fix the earlier task that failed, then submit this one again',
    'lost': 'Submit it again',
    'unknown_job': 'Reload the page and check the task list; submit again if needed',
    'not_found': 'Reload the page and make sure it still exists, then try again',
    'source_missing': 'Restore or regenerate the previous video, then continue it',
    'chain_missing': 'Continue again from the finished video instead',
    'not_resumable': 'Submit a new task',
    'chain_unavailable': 'Restart FreeVideo from the launcher, then try again',
    'continuation_unavailable': 'Choose another way of generating, or generate the whole video again',
    'join_failed': 'Check the free disk space, then export the film again',
    'invalid_output': 'Check the free disk space, then try again',
    'storage_unavailable': 'Check the free disk space and the write access to the folder, then try again',
    'disk_full': 'Free up disk space, then try again',
    'submit_interrupted': 'Check the task list to see whether it started; submit again if it did not',
    'timeout': 'Try again; if it happens again, use a shorter length or a smaller size',
    'invalid_media': 'Use a file that plays normally, then try again',
    'clip_unreadable': 'Generate the character reference video again, then try again',
    'invalid_request': 'Change the settings to a supported range, then try again',
    'invalid_drama': 'Check the drama settings, then try again',
    'reshoot_limit': 'Choose one of the existing takes, or add a new shot',
    'production_limit': 'Finish or delete tasks you no longer need before continuing',
    'export_failed': 'Check the free disk space, then export again',
    'chain_too_long': 'Shorten what you continue, or use fewer parts, then try again',
    'chain_short_parts': 'Merge or regenerate the parts shorter than 10 seconds, then try again',
    'chain_rejected': 'Change the size or the length, then try again',
    'local_only': 'Do this on the computer that runs FreeVideo',
    'page_required': 'Open the FreeVideo page in the browser, then do this there',
    'conflict': 'Wait for the current task to end, or reload the page, then try again',
    'invalid_edit': 'Check the edit settings, then try again',
    'invalid_screenplay': 'Change the screenplay as indicated, then try again',
    'unsupported_media': 'Use a picture instead, then try again',
    'unknown_shot': 'Reload the page and choose an existing shot',
    'unknown_take': 'Reload the page and choose a take that this shot has',
    'take_not_ready': 'Generate this take again, or choose another one',
    'chosen_by_user': 'To change it, choose another take on the page',
    'nothing_to_cancel': 'Nothing to do; submit a new task when needed',
    'not_allowed': 'Stop or continue this drama on the page',
    'already_ended': 'Start a new drama when needed',
    'needs_confirmation': 'Confirm first, then run this action',
    'declined_by_user': 'Ask for this action again when needed',
    'stale_revision': 'Reload the page to see the latest content, then change it again',
    'changed_by_user': 'Continue from the content you edited by hand, or reload the page and try again',
    'card_in_use': 'Wait for the drama to end, or stop it first, then change this card',
    'project_changed': 'Reload the page to see the latest content, then change it again',
    'image_changed': 'Reload the page to see the latest content, then change it again',
    'speech_language': 'Use a sound in a supported language, or write the subtitles by hand',
    'listen_failed': 'Try again, or write the subtitles by hand',
    'music_failed': 'Try again',
    'stems_failed': 'Try again',
    'sound_failed': 'Try again',
    'voice_failed': 'Try again',
    'small_memory': 'Use picture generation on a Mac with 16 GB of memory or more',
}
FAILURE_MESSAGES_ZH = {
    'The previous video is too short to continue; it needs to be at least 1.7 s long.': '接续的上一段视频太短，至少需要 1.7 秒',
    'The previous video has no usable continuation data. Generate it again with this version of FreeVideo.': '上一段视频缺少可用的接续数据',
    'The previous video cannot be read.': '接续的上一段视频无法读取',
    "The previous video's frame rate is too low to continue; it needs at least 10 frames per second.": '接续的上一段视频帧率太低，每秒至少需要 10 帧',
    'Use the same aspect ratio as the previous video.': '接续的画面比例与上一段视频不同',
    'The reference video is too short; it needs to be at least about 1 s long.': '参考视频太短，至少需要约 1 秒',
}


def _failure_line(error):
    lines = str(error.get('message') or '').strip().splitlines()
    line = lines[0] if lines else ''
    if line.startswith('FreeVideo generation failed') and len(lines) > 1:
        # The engine puts the child's last error here as "Cause: <error>" (failure_details).
        line = re.sub(r'^Cause:\s+', '', lines[1].strip())
    return re.sub(r'^(?:[\w.]*Error|[\w.]*Exception):\s*', '', line)


def failure_reason(error):
    if isinstance(error.get('error_zh'), str) and error['error_zh'].strip():
        return error['error_zh']
    line = _failure_line(error)
    if line in FAILURE_MESSAGES_ZH:
        return FAILURE_MESSAGES_ZH[line]
    if line.startswith(('CUDA out of memory', 'MPS backend out of memory')):
        return FAILURE_REASONS_ZH['out_of_memory']
    return FAILURE_REASONS_ZH.get(error.get('code'), '没有完成')


# The same lookup in English for the page: the specific sentences are already written for people.
def failure_reason_en(error):
    if isinstance(error.get('error_en'), str) and error['error_en'].strip():
        return error['error_en']
    line = _failure_line(error)
    if line in FAILURE_MESSAGES_ZH:
        return line
    if line.startswith(('CUDA out of memory', 'MPS backend out of memory')):
        return FAILURE_REASONS_EN['out_of_memory']
    return FAILURE_REASONS_EN.get(error.get('code'), 'Did not finish')


# The people-facing fields reasons() writes; next_zh/next_en only when the reason has a next step.
REASON_KEYS = ('error_zh', 'error_en', 'next_zh', 'next_en')


def reasons(error):
    from ..other_usage import fact, join
    result = dict(error_zh=failure_reason(error), error_en=failure_reason_en(error))
    code = error.get('code')
    step = code in FAILURE_NEXT_ZH and result['error_zh'] == FAILURE_REASONS_ZH.get(code)
    for key, zh in (('error_zh', True), ('error_en', False)):
        suffix = fact(error.get('other_usage'), zh=zh)
        if suffix and suffix not in result[key]:
            result[key] = join(result[key], suffix, zh=zh)
    if step:
        result.update(next_zh=FAILURE_NEXT_ZH[code], next_en=FAILURE_NEXT_EN[code])
    return result


def capture_other_usage(error):
    from ..other_usage import at_failure, fact, join, MEMORY_ERRORS
    if error and error.get('code') in MEMORY_ERRORS and 'other_usage' not in error:
        error = dict(error, other_usage=at_failure(error['code']))
    if error and error.get('other_usage') and error.get('reason'):
        error = dict(error, reason=dict(error['reason']))
        for lang in ('en', 'zh'):
            suffix = fact(error['other_usage'], zh=lang == 'zh')
            if suffix and suffix not in error['reason'].get(lang, ''):
                error['reason'][lang] = join(error['reason'].get(lang, ''), suffix, zh=lang == 'zh')
    return error


def with_reason(error):
    return dict(error, **reasons(error)) if error else error


def build(code, *, details=None, request=None, validation=False, message=None, next_action=None,
          action_required=True, retry_after_seconds=None, owner=None, kind=None, stage='request', resumable=True,
          report_id=None, memory=None, image_stage=None, other_usage=_UNREAD_USAGE):
    code = code if code in ERRORS else 'internal_error'
    _, retryable, default_message, _ = ERRORS[code]
    action = recovery(code, kind=kind, stage=stage, resumable=resumable)
    if kind == 'image':
        default_message = IMAGE_ERRORS.get(code, default_message)
    if kind == 'session' and code == 'not_found':
        default_message = SESSION_GONE[0]
    result = dict(code=code, message=message if validation and message else default_message,
                  details=(details or []) if validation else safe_details(details or []),
                  retryable=retryable, request_id=request or request_id(),
                  docs='/freevideo/agent/v1/guide#errors')
    if kind == 'image':
        result.update(image_failure(code, report_id))
        if code == 'no_memory' and memory == 'host':
            result['memory'] = 'host'
        if image_stage in IMAGE_STAGES:
            result['stage'] = image_stage
    if other_usage is not _UNREAD_USAGE:
        result['other_usage'] = other_usage
    result = capture_other_usage(result)
    if action_required:
        result['next'] = next_action if validation and next_action else action
    if code == 'capabilities_computing':
        result['retry_after_seconds'] = retry_after_seconds or 1
    if code == 'busy':
        result['retry_after_seconds'] = retry_after_seconds or 1
        result['details'].append(dict(owner=owner if owner in ('agent', 'ui') else 'ui'))
        result['message'] += ' An Agent job is ahead.' if owner == 'agent' else ' A job in the user interface is ahead.'
    return with_reason(result)


def validation(errors, code='invalid_request'):
    from .schema import diagnostic
    rows = [e if e.get('_safe') else diagnostic('/<unknown>', 'a valid value', None) for e in errors]
    rows = [{k: v for k, v in e.items() if k != '_safe'} for e in rows]
    return build(code, details=rows, validation=True,
                 message='; '.join(e['message'] for e in rows),
                 next_action=' '.join(dict.fromkeys(e['next'] for e in rows)))


def text(error):
    """One line for the Agent; a plain-text error (a failed white-model scene) is its own message."""
    if not isinstance(error, dict):
        return str(error).strip() or 'failed: The request did not finish.'
    reason = error.get('reason') or {}
    code = error.get('code') or 'failed'
    message = reason.get('en') or error.get('message') or ''
    diagnostic = image_failure(code, error.get('report_id'))['report_id'] if reason else None
    return '%s: %s%s%s' % (code, message,
                          ' Diagnostic report: /freevideo/image-gen/report/' + diagnostic if diagnostic else '',
                          ' Next: ' + error['next'] if error.get('next') else '')
