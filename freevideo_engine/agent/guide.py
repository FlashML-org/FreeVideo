"""What an agent needs to write good FreeVideo prompts, served by GET /freevideo/agent/v1/guide.

Summarised from MiniMax's h3-prompt-writing skill (linked below) and FreeVideo's
own prompt rules; the enhancement job applies the same contract.
"""
from ..default_prompt import DEFAULT_PROMPT
from .schema import CAST_OPERATIONS, DRAMA_OPERATIONS, IMAGE_OPERATIONS, PROJECT_OPERATIONS, SESSION_OPERATIONS

SOURCE = 'https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing'

GUIDE = {
    'source': SOURCE,
    'language': 'Write the sections in English. Keep dialogue, lyrics and visible on-screen text verbatim in '
                'their original language.',
    'tasks': {
        't2va': 'Text only: describe the whole audiovisual timeline.',
        'i2va': 'media.first: the image is the frame at 0.00 s; start from its style, subjects and layout, then '
                'develop forward.',
        'fl2va': 'media.first + media.last: describe one continuous path from the first frame to the last; '
                 'prefer a single shot.',
        'l2va': 'media.last: infer a plausible opening and converge on the image at the end.',
        'ref2va': 'media.references (images, video, audio; experimental): use the six reference sections and '
                  'label every subject.',
    },
    'sections': {
        'base': ['integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music'],
        'ref2va': ['subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
                   'overall_soundscape', 'non_diegetic_music'],
        'format': 'Each section starts on its own line as "name: text", separated by one blank line, in this order.',
    },
    'shots': [
        'Begin the description with [Shot 1] and the visual style and opening composition '
        '(e.g. "Live-action, cinematic, a medium-wide shot frames ...").',
        'Later shots: "[Shot 2] At 00:03.500, the camera cuts to ...". Cut times strictly increase and stay inside '
        'the duration. Cut only for new information; use camera motion for small changes.',
        'Camera motion = type + optional amplitude + speed, written as prose: push in / pull out, pan, truck, tilt, '
        'pedestal, arc shot, tracking shot, static shot, zoom, POV, roll; "with small amplitude", "at slow speed".',
    ],
    'dialogue': [
        'Speakers get stable IDs (S1), (S2) across shots; describe each voice when it first appears.',
        'Spoken words go inside <d>[Language] ...</d>, verbatim: The woman (S1) says: <d>[Chinese] 我们走吧。</d>',
        'Voiceover: "says in an off-screen voiceover: <d>...</d> while his lips remain completely closed."',
        'Visible text goes in double quotes: a neon sign reading "营业中". Never put spoken words in quotes: '
        'quoted words are drawn on screen.',
        'H3 may still caption spoken lines. When the shot should have no captions, end the description with '
        '"No subtitles, no on-screen text." (or "No subtitles or captions." when the shot shows a sign or screen); '
        'it still happens now and then, so ask the VLM whether text is drawn '
        'over the picture and retake with another seed. A captioned frame that seeds the next shot can be cleaned '
        'with the edit recipe operation remove_text.',
    ],
    'sound': [
        'overall_soundscape: 1-4 sentences of ambience, action sounds and breaths; N/A only for silence.',
        'non_diegetic_music: 1-3 sentences of instruments, tempo and dynamics, or N/A.',
    ],
    'references': [
        'Tags are numbered per kind in reference order: <Picture 1>, <Picture 2>, <Video 1>, <Audio 1>. '
        'For keyframes the first frame is <Picture 1> and the last frame the next picture.',
        'Name subjects <Subject 1>, <Subject 2> in subject_definitions and reuse those labels in every section.',
        'Clips are cut to the generated length (at most 15 s); the job result lists any cut.',
        'A reference video can supply motion, camera, or acting. Assign jobs in the prompt: '
        '"<Picture 1> is identity; <Video 1> is walking motion and timing only — do not copy the person, '
        'clothes, or room in <Video 1>." This is prompt-assigned Ref2V, not skeletal motion control. '
        'Trim the clip to the useful 2-15 s. Identity can leak from the video; say what to ignore.',
    ],
    'timing': [
        'Single-block frames are 24 fps on a 17n+5 grid (6 s -> 6.583 s, 10 s -> 10.125 s). '
        'Generation uses the continuous backend. A request of up to 15 s is one generation; a longer one is made '
        'in parts of up to 15 s (30 s is 15 + 15) that continue one another and join automatically. '
        'Continuing (longer than 15 s, or continue_from) needs two-pass text generation: quality "light" '
        'or sampling.two_pass=true, no preview and no media. '
        'Multi-block output is cut to the planned duration. POST /plan shows the chain plan. '
        'A continue_from job saves a new video that holds only the added part; to hand the user one video, '
        'insert both into a project and export it.',
        'first_frame_reference (default false) reduces drift in a continued video: each continuing part also '
        'sees the first frame of the whole video. Set it when one scene with the same people runs over several '
        'parts and framing, colour or the person may wander. Leave it off for camera moves, big action or a '
        'scene that should change: it holds framing and motion close to the first frame. Continuing parts take '
        'somewhat longer with it; a single-part video is unaffected.',
        'Keep the described timeline inside the duration. 4-15 s is the tested range; longer is experimental. '
        'Spoken lines need time: about 2.4 English words per second at a film pace, plus a beat of action '
        'before they speak. One word ("Hi") fits a 1.625 s clip; a long greeting needs about 5 s. '
        'Studio Spoken dialog estimate is only a guess.',
        'Width and height are multiples of 32. For a size the user names that is not, use the nearest multiple '
        'on each side (1000x560 -> 992x544 or 992x576); a rejected request names them.',
    ],
    'agent_jobs': [
        'GET /capabilities reads the studio cache with the same size/task/material query parameters. '
        'status=computing includes retry_after_seconds; unknown plan estimates have seconds=null.',
        'GET /jobs/{id}?wait=25 waits at most 25 seconds and returns poll_after_ms. Like POST /jobs, the answer '
        'has the job under job (its fields are also at the top level). Poll the same ID until job.status is '
        'succeeded, failed, cancelled or interrupted; disconnecting a wait or SSE does not cancel generation.',
        'Jobs wait behind an active studio chain. GET /queue gives Agent/UI ownership and counts ahead, '
        'without studio prompts. POST /jobs/{id}/cancel cancels only this job.',
        'POST /jobs/{id}/resume continues failed, interrupted or cancelled generation; successful blocks stay. '
        'refine_from is the separate preview second pass. Active or succeeded jobs cannot resume.',
        'Results include the joined library_id and each block library_id/report_id. Fetch videos and thumbnails '
        'with the token at /library/{day}/{run}/file and /thumbnail. MCP freevideo_asset and CLI agent asset use HTTP.',
    ],
    'editing_projects': [
        'GET /projects lists timelines shared with the editor page. First GET /projects/{id} to read revision and clip IDs.',
        'POST /projects/{id}/operations sends revision and 1-64 operations, using whole frames at 24 fps. The batch saves once or changes nothing.',
        'On 409 project_changed, recompute edits from error.project and its current_revision, then send a new request. On clip_busy wait for generation to finish.',
        'POST /projects/{id}/generate reserves pending parts for clip_ids or a start/frames range and fills them as the returned job finishes. POST /projects/{id}/export renders the revision you read; wait for its job.',
        'Subtitles: POST /projects/{id}/listen with mode transcribe (writes down what is said) or align (times the lines you give) and the revision you read; wait for the job and read result.lines, each with start and end frames. Then add them with a subtitles operation of cues {start, frames, text}; replace true swaps the whole track. With no speech model the job fails with no_speech_model: write the lines yourself.',
        'Reuse ref or Idempotency-Key only for the identical request. Source video/audio/thumbnail URLs require the same Agent token.',
    ],
    'project_routes': [dict(operation=name, method=method, path=path, description=summary)
                       for name, (method, path, summary) in PROJECT_OPERATIONS.items()],
    'editing_images': [
        'POST /images opens a picture as the image editor does; GET /images/{id} reads its document (layers), selection and revision.',
        'POST /images/{id}/operations sends revision and 1-64 operations run with the page\'s own commands; the batch saves once or changes nothing. Layer IDs of new layers are in results[i].new_layer_ids.',
        'Positions and sizes are document pixels; brush points and sizes are fractions of the layer. Repair a picture with heal or clone on its own layer, even when it is locked: each stroke is a step of that layer that undo removes, and the source file never changes. '
        'A new paint layer is empty, so heal and clone there have nothing to work from; paint and gradients go on a paint layer above a locked picture.',
        'On 409 image_changed, recompute from error.image and its current_revision. POST /images/{id}/render makes a PNG input file from the revision you read; use the job result file as a picture source.',
    ],
    'image_routes': [dict(operation=name, method=method, path=path, description=summary)
                     for name, (method, path, summary) in IMAGE_OPERATIONS.items()],
    'sessions': [
        'Create a work conversation with POST /sessions; share a sentence using POST /sessions/{id}/events with type=message and text.',
        'Read /sessions/{id}/events?after=<last event seq>&wait=25 for user messages or confirmation answers. With Accept: text/event-stream, use Last-Event-ID to reconnect.',
        'Only the page user may stop or resume. On stopped_by_user, wait for the user to continue; do not start another session to bypass the stop.',
        'On needs_confirmation nothing changed: wait with freevideo_wait (no id) for the answer, then repeat the same call unchanged. '
        'On declined_by_user do not repeat it; change the plan or ask the user. GET /confirmations shows what asks first.',
    ],
    'session_routes': [dict(operation=name, method=method, path=path, description=summary)
                       for name, (method, path, summary) in SESSION_OPERATIONS.items()],
    'filming': [
        'Filming is for a film of several shots, each picked from takes. One video of up to 60 seconds is one generate '
        'job (longer requests continue and join automatically); to polish an idea first, run enhance, then generate. '
        'A film is cut into shots: see the rule "Cut by default" before making one long continued video.',
        'Use drama plan → start → get → sheet → choose/reshoot → finish; inspect todo after every change.',
        'At start tell the user the estimated total time; on over_budget decide whether to ask them; '
        'without an estimate report only the current work and elapsed time.',
        'Use get with since=revision and wait=25. changed=false is a compact timeout; retain the last full board.',
        'If paused is set, stop acting and long-wait until the user continues. Only the user may pause/resume.',
        'Continuations wait for the preceding chosen take and use real video continuation; upstream changes reconnect downstream shots.',
        'Reuse ref or Idempotency-Key with identical content for safe replay. External callers cannot supply by.',
        'GET /drama?status=active and connect/hello unfinished identify films to continue. No built-in director loop starts automatically.',
    ],
    'short_drama': [
        'One job = one shot or a few cuts of 5-10 s. Keep character descriptions identical across shots '
        '(same words for face, hair, clothing) and give each speaker the same voice description.',
        'Text alone does not keep a face identical across cuts. For a new scene with the same character, start '
        'from an image of them: a frame of an earlier shot edited into the new framing (media.first with edit), '
        'or a character picture as a reference (experimental).',
        'Continuity: start the next shot from the previous shot\'s last frame: '
        'media.first = {"from_job": "<id>", "frame": "last"}.',
        'Vertical short drama: aspect "9:16" (768x1344 at the native 0.98 MP).',
        'Review: ask the local VLM about sampled frames (kind "vlm", questions[].video) and regenerate weak shots '
        'with another seed (takes) before assembling (kind "assemble").',
        'Use quality "light" with preview=true to check a shot quickly, then refine_from that job.',
    ],
    'example': DEFAULT_PROMPT,
}


# How an agent is expected to work once a user has asked for something. Only what this version can honour.
RULES = [
    'Finish the job. When the user asks for a video, keep going until the finished video exists. Decide ordinary '
    'things yourself: how to split shots, how many takes, which take to keep. Tell the user what you decided in '
    'one or two sentences at the end, not along the way. Name every video you submit: label is a short name of what '
    'it shows, in the user\'s language (2 to 8 Chinese characters, or 1 to 4 words), for example "Seaside at dawn".',
    'Solve problems yourself first. Every error has a next field, and often details with alternatives; these are '
    'steps that work on this computer. Not enough graphics memory: use a listed alternative (a lower resolution '
    'or a shorter video) and try again. A take is not good: make another; if it is still not good after two more, '
    'describe it a different way instead of repeating the request. The queue is busy: wait with wait=25 on the '
    'job, never in a tight loop. The connection dropped or FreeVideo restarted: connect again, read the job list, '
    'and continue; resend an unanswered request with the same ref, and never generate again what is already done.',
    'Do not stop to report progress. A long task is not a reason to stop or to ask whether to go on.',
    'When a video is done, tell the user it is in "Your creations" in FreeVideo, in plain words. Do not read out job, library '
    'or project ids; give a file path only when the user asks for one. If the length differs from what was asked, '
    'say the real length in seconds and do not explain frame counts.',
    'Ask the user only when you would have to change the content or style they asked for, when the work will '
    'take clearly longer than you told them at the start, or when you would have to delete or overwrite their '
    'own works or edits.',
    'Stay inside FreeVideo. Do not touch other files on this computer. Never change back something the user '
    'edited. When the user tells you to stop, stop: cancel what you started and start nothing new.',
    'Cast before you shoot. Search the cast library first. Use the card the user named, or one that clearly '
    'matches; ask if you are unsure. Create a card only when none fits: from the user\'s photos if they gave any, '
    'otherwise make its reference pictures with POST /cast/{id}/generate. '
    'Before shooting, tell the user the casting in one sentence. Later shots use the card\'s current pictures. '
    'Keep cards you made so later films can reuse them.',
    'Cut by default. Wherever the film can change shot, make the new shot from the reference pictures it should '
    'have (cast cards, scene pictures) instead of continuing the previous video. Continue a video only for a '
    'long take that cannot be cut, an action that must stay unbroken, or sound that must stay unbroken, and '
    'continuing again and again is not recommended. Content of 16 to 29 seconds is better made as shots of '
    'up to 15 seconds each. One video has at most 5 parts (the first and 4 '
    'continuations), each continued part at least 10 seconds, about 70 seconds in all; filling that is not '
    'recommended. Past it, cut to a new shot.',
]


RULES_ZH = [
    '把任务做完。用户要求生成视频时，持续推进，直到成片生成。如何拆分镜头、拍几条、选哪一条等普通决定，'
    '由你自己做。最后用一两句话告诉用户你做了哪些决定，不要每做一步都请用户决定。'
    '提交的每条视频都起个名字：label 写拍的是什么，用用户的语言，2 到 8 个字，例如“清晨海边”。',
    '先自己解决问题。每个错误都有 next 字段，details 里也常有可选方案；这些是在本机可行的处理步骤。'
    '显存不足时，从列出的方案中选择较低分辨率或较短时长重试。一条拍得不好就再拍；再拍两条仍不满意，'
    '换一种描述，不要反复提交同一个要求。队列繁忙时，用 wait=25 等待该任务，不要不停查询。'
    '连接中断或 FreeVideo 重启后，重新连接，读取任务列表并继续；未收到响应的请求用相同的 ref 重发，'
    '已经完成的内容不要重新生成。',
    '不要为了汇报进度而停下。任务耗时较长，不是停止工作或询问是否继续的理由。',
    '视频做好后，用通俗的话告诉用户它已经放进 FreeVideo 的“我的作品”里。不要念任务、作品或剪辑的编号；'
    '文件路径在用户问起时再给。实际长度和要求的不一样时，只说实际约几秒，不解释帧数。',
    '只有需要改变用户要求的内容或风格、预计耗时明显超过开始时告诉用户的时间，'
    '或者需要删除或覆盖用户自己的作品或修改时，才询问用户。',
    '只在 FreeVideo 内工作，不要改动本机的其他文件，不要撤销用户做的修改。'
    '用户要求停止时，取消你启动的任务，不再启动新任务。',
    '先选角，再拍摄。先搜索角色库，使用用户指定的卡片或明确符合要求的卡片；拿不准时询问用户。'
    '只有找不到合适卡片时才新建：用户提供了照片就用照片，否则通过 POST /cast/{id}/generate 生成参考图。'
    '开拍前用一句话告诉用户选角结果。后续镜头使用卡片当前的图片。保留你创建的卡片，方便以后拍片复用。',
    '默认切镜头。能切镜头的地方就切：新镜头直接用对应的参考图（人物卡、场景图）生成，不接上一段视频。'
    '只有不能切镜的长镜头、必须连贯的动作、必须连贯的声音，才接着上一段生成，而且不建议一段接一段地连续接。'
    '一条视频最多 5 段（第一段加 4 次接着生成），接着生成的每一段至少 10 秒，总长约 70 秒；不建议接满，超过就切镜头。',
]


# Connection copy only: keep translations out of the shared contracts and MCP tools/list.
CAPABILITIES_ZH = {
    ('GET', ''): '查看可用功能、就绪状态和队列',
    ('GET', '/guide'): '阅读提示词和接口使用指南',
    ('GET', '/schema'): '查看任务的数据结构',
    ('GET', '/openapi.json'): '查看接口契约',
    ('GET', '/capabilities'): '查看设备能力',
    ('POST', '/plan'): '校验生成请求并预览生成计划',
    ('POST', '/jobs'): '提交视频生成、提示词增强、视觉问答或片段拼接任务',
    ('GET', '/jobs'): '查看任务列表',
    ('GET', '/jobs/{id}'): '查看任务进度和结果',
    ('GET', '/jobs/{id}/events'): '持续接收任务进度',
    ('POST', '/jobs/{id}/cancel'): '取消任务',
    ('POST', '/jobs/{id}/resume'): '接着生成一条视频里还没完成的部分',
    ('POST', '/media'): '上传图片、视频或音频内容',
    ('GET', '/media/{kind}/{file}/file'): '下载上传或处理后的素材',
    ('POST', '/media/inspect'): '查看素材信息',
    ('POST', '/media/frame'): '提取视频的一帧',
    ('POST', '/media/contact-sheet'): '查看带时间标记的视频帧拼图',
    ('POST', '/media/edit'): '按编辑配方处理图片',
    ('GET', '/library'): '搜索已保存的视频',
    ('GET', '/library/{day}/{run}'): '查看已保存的视频信息',
    ('GET', '/library/{day}/{run}/file'): '下载已保存的视频',
    ('GET', '/library/{day}/{run}/thumbnail'): '查看已保存的视频缩略图',
    ('GET', '/queue'): '查看队列顺序及任务属于 Agent 还是用户',
    ('GET', '/editor/sources/content'): '下载剪辑项目的源素材或缩略图；使用 GET /projects/{id} 返回的素材标识',
    ('GET', '/inbox'): '等待当前工作会话中用户的消息或答复，最多 50 秒；again=true 表示暂时没有，再调一次',
    ('POST', '/drama/plan'): '预览多镜头影片的剧本和各镜头拍摄条数，不保存、不提交。'
        '多镜头影片可以逐镜选片；单条视频（最长 60 秒）用一个 generate 任务',
    ('POST', '/drama'): '确认计划后开始拍摄，进度会保存；查看各条画面并逐镜选片',
    ('GET', '/drama'): '查看影片列表；status=active 返回未完成影片的摘要',
    ('GET', '/drama/{id}'): '读取待办事项 todo 和版本 revision；用 since 和 wait=25 等待变化。'
        'paused 有值时停止操作，持续等待用户继续',
    ('GET', '/drama/{id}/shots/{shot}/sheet'): '各条生成完成后，查看带时间标记的画面和前一镜所选片段的末帧',
    ('POST', '/drama/{id}/shots/{shot}/choose'): '查看画面后选择一条；选定后才能拍摄承接它的下一镜',
    ('POST', '/drama/{id}/shots/{shot}/reshoot'): '按修改要求重拍，再查看画面并重新选片',
    ('POST', '/drama/{id}/finish'): '每一镜都选定后提交拼接，继续查询影片直到成片就绪',
    ('POST', '/drama/{id}/cancel'): '取消尚未完成的拍摄，已完成的各条仍可使用',
    ('POST', '/drama/{id}/pause'): '只有用户可以暂停影片拍摄；外部 Agent 调用会返回 not_allowed',
    ('POST', '/drama/{id}/resume'): '只有用户可以继续影片拍摄；外部 Agent 调用会返回 not_allowed',
    ('POST', '/cast'): '没有合适的现有卡片时，创建角色或场景卡片',
    ('GET', '/cast'): '选角前搜索角色库，复用符合要求的卡片',
    ('GET', '/cast/{id}'): '查看卡片的图片、声音和版本',
    ('PATCH', '/cast/{id}'): '修改卡片时提交读取到的 revision；保留用户修改过的字段',
    ('DELETE', '/cast/{id}'): '删除你创建且没有未完成影片使用的卡片；其他情况先询问用户',
    ('POST', '/cast/{id}/generate'): '用户没有提供图片时，按描述生成参考图；任务结束后重新读取卡片',
    ('GET', '/projects'): '查看与剪辑页面共用的项目列表，再读取具体项目的 revision 和片段标识',
    ('GET', '/projects/{id}'): '读取时间线和需要鉴权的源素材地址；编辑前先读取 revision 和片段标识',
    ('POST', '/projects'): '创建剪辑时间线；提交有效的剪辑项目，id 为 null，revision 为 0',
    ('PUT', '/projects/{id}'): '替换时间线；先读取项目，保留 id 和 revision。'
        '遇到 project_changed 时，根据 error.project 重新计算修改',
    ('POST', '/projects/{id}/operations'): '一次应用 1–64 项时间线编辑，全部成功才保存一次。'
        '先读取 revision 和片段标识；遇到 project_changed 时根据 error.project 重新计算，'
        '遇到 clip_busy 时等待生成完成',
    ('POST', '/projects/{id}/generate'): '为指定片段或帧范围生成新的画面和声音，效果与页面的“生成”相同。'
        '先读取 revision 和片段标识；所选部分会立即预留，返回的任务完成后填入结果',
    ('POST', '/projects/{id}/export'): '将保存的某个版本导出为 MP4，存入已保存视频。'
        '先读取 revision，等待返回的任务完成，再读取结果视频',
    ('POST', '/projects/{id}/listen'): '听剪辑工程的声音，得到字幕行及其时间：对给定字幕行定时，或直接转写。'
        '先读取 revision，等待返回的任务完成，再用 subtitles 编辑把字幕行加入时间线；'
        '没有安装语音模型时返回 no_speech_model，改为自己写字幕行',
    ('POST', '/images'): '像图片编辑页面一样打开图片；提交 source，会话会保留源图片副本和图层文档',
    ('GET', '/images/{id}'): '读取图片编辑会话的图层文档、选区和 revision；编辑前先读取 revision 和图层标识',
    ('PUT', '/images/{id}'): '替换整个图片文档；先读取会话并提交其 revision。'
        '遇到 image_changed 时根据 error.image 重新计算修改',
    ('POST', '/images/{id}/operations'): '使用页面相同的命令，一次应用 1–64 项图片编辑，全部成功才保存一次。'
        '先读取 revision 和图层标识；遇到 image_changed 时根据 error.image 重新计算',
    ('POST', '/images/{id}/render'): '将保存的某个版本渲染为 PNG 输入文件；等待返回的任务完成，'
        '再使用结果文件作为图片素材',
    ('POST', '/sessions'): '创建工作会话，与用户共享消息和计划',
    ('GET', '/sessions'): '查看工作会话列表，最近更新的排在前面',
    ('GET', '/sessions/{id}'): '读取工作会话信息，查看用户是否已停止该会话',
    ('GET', '/sessions/{id}/events'): '读取指定序号之后的事件；最多等待 25 秒，也可请求 SSE 事件流',
    ('POST', '/sessions/{id}/events'): '向用户发送消息或计划；操作步骤由服务端记录',
    ('POST', '/sessions/{id}/stop'): '只有页面用户可以停止工作会话',
    ('POST', '/sessions/{id}/resume'): '只有页面用户可以继续工作会话',
    ('GET', '/confirmations'): '查看哪些 Agent 操作需要等待用户确认',
    ('PUT', '/confirmations'): '只有页面用户可以修改需要确认的操作',
}


def installation_language(root=None):
    """Use saved interface preferences, with English when they cannot be read."""
    from ..launcher_settings import Store
    from .client import default_root
    try:
        language = Store(root if root is not None else default_root()).read().get('language', '')
        return 'zh' if language.lower().startswith('zh') else 'en'
    except (OSError, ValueError, TypeError, RuntimeError, AttributeError):
        return 'en'


_PAGE_LANGUAGE = []


def page_language(lang=None):
    """The language the connect card last showed its sentence in; None until the page has shown it.

    The sentence carries no parameter, so the instructions an agent then opens follow what the user saw.
    """
    if lang in ('zh', 'en'):
        _PAGE_LANGUAGE[:] = [lang]
    return _PAGE_LANGUAGE[0] if _PAGE_LANGUAGE else None


def connection_discovery(root, base, lang=None):
    from . import API_VERSION
    from .schema import PREFIX
    lang = lang or page_language() or installation_language(root)
    connect = base + PREFIX + '/connect?lang=' + lang
    return dict(enabled=True, api_versions=[API_VERSION], base=PREFIX, auth='bearer',
        token='FreeVideo 安装目录中的 agent/token' if lang == 'zh' else
              'agent/token in the FreeVideo installation folder',
        connect=connect, instructions='通过 GET ' + connect + ' 阅读纯文本接入说明，并按说明操作。'
        if lang == 'zh' else 'GET ' + connect + ' for plain-text connection instructions and follow them.')


def introduction(zh, offers):
    """What the agent tells the user first: only what this installation is known to carry through."""
    rows = ['按描述生成视频，可以给首帧图片或参考图' if zh else
            'Make a video from a description, optionally from a first-frame picture or reference pictures']
    if offers.get('editing'):
        rows.append('剪辑：把片段排上时间线，分割、裁剪、删除，再导出成片' if zh else
                    'Edit: put clips on a timeline, split, trim and remove them, then export the finished video')
    return rows


def connection_guide(root, base, lang=None, offers=None):
    """Public bootstrap text contains locations and commands, never credential values."""
    import json
    from pathlib import Path
    from .mcp import client_config
    from .schema import PREFIX, contracts
    lang = lang or page_language() or installation_language(root)
    zh = lang == 'zh'
    config = client_config(root)
    token_file = str(Path(root).resolve() / 'agent' / 'token')
    # The executable/API/credential locations come from this running installation.
    example = '\n'.join([
        'import json, pathlib, urllib.request',
        'base = ' + repr(base + PREFIX),
        'token = pathlib.Path(' + repr(token_file) + ').read_text().strip()',
        'def call(method, path, body=None):',
        '    data = None if body is None else json.dumps(body).encode()',
        '    req = urllib.request.Request(base + path, data=data, method=method,',
        '        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})',
        '    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))',
        '    with opener.open(req) as response: return json.load(response)',
        '# client is the name the user sees as connected: put your own, for example "Claude Code" or "Codex".',
        'call("POST", "/connect/hello", {"client": "Local Agent", "via": "http"})',
        'print(call("GET", ""))',
        '# Only after the user asks for a video:',
        '# label is the short name the user sees on the job, in their language: what the video shows, 2 to 8 characters.',
        '# seconds and aspect are what the user asked for (10 seconds and 16:9 when they said nothing): send them the first time.',
        '# ref is a new name for every new request, with the date and time in it; the same ref again only repeats a submission whose answer was lost.',
        '# job = call("POST", "/jobs", {"prompt": "USER_REQUEST", "seconds": 5, "aspect": "16:9", "label": "SHORT NAME",',
        '#                               "ref": "A-NEW-NAME-FOR-THIS-REQUEST"})["job"]',
        '# while job["status"] not in ("succeeded", "failed", "cancelled", "interrupted"):',
        '#     job = call("GET", "/jobs/" + job["id"] + "?wait=25")["job"]',
        '# Download base origin + job["result"]["video"]["url"] with the same Authorization header.',
        '# The conversation inside FreeVideo (see below): each wait is its own command; when it returns, run the next one.',
        '# box = call("GET", "/inbox?wait=50")   # {"session": ..., "inbox": [what the user said], "again": true if nothing yet}',
        '# call("POST", "/sessions/" + box["session"] + "/events", {"type": "message", "text": "YOUR WORDS"})',
        '# Post what you say first, then wait: never one command that waits and then posts, your words would be up to 50 s late.',
    ])
    return dict(language=lang, api=base + PREFIX, token_file=token_file, claude_code=config['claude_code'],
        codex=config['codex'], mcp_json=config['mcp_json'], command=config['command'],
        authentication='读取令牌文件，将内容放入 Authorization: Bearer 请求头。不要打印令牌、展示给用户，'
                       '也不要写入其他文件。' if zh else
                       'Read the token file and put its contents in the Authorization: Bearer header. '
                       'Do not print it, show it to the user, or write it to another file.',
        session='新配置的工具通常要到下一个会话才能用；当前会话请直接使用本机 HTTP 接口。' if zh else
                'Newly configured tools are usually unavailable in the current session. Use this local HTTP '
                'API in this session; the tools will be available in your next session.',
        hello=dict(method='POST', url=base + PREFIX + '/connect/hello', body=dict(client='Local Agent', via='http')),
        capabilities=[dict(method=method, endpoint=PREFIX + suffix,
                           description=CAPABILITIES_ZH.get((method, suffix), row['summary']) if zh else row['summary'])
                      for (method, suffix), row in contracts().items()
                      if not suffix.startswith('/freevideo/') and not suffix.startswith('/connect')],
        first_reply='用中文回复用户；如果用户改用其他语言，就跟随用户。连接后先用通俗的话告诉用户你能做“先介绍这几样”里的事，'
                    '只说这几样，并举两三个用户可以提出的要求。不要报版本号，不要念接口名。'
                    '不要自行启动任务，等待用户提出要求。' if zh else
                    'Reply to the user in English; if the user switches languages, follow their language. '
                    'After connecting, first reply in plain words: tell the user you can do the things under '
                    '"Introduce these", only those, and give two or three requests they can say. Do not quote version '
                    'numbers or endpoint names. Do not start any task yourself; wait for the user to ask.',
        introduce=introduction(zh, offers or {}),
        conversation='用户会在 FreeVideo 的“AI 全自动”页里直接和你说话，你的话也显示在那里。'
                     '连接后先调一次等待接口 GET ' + base + PREFIX + '/inbox?wait=50（MCP 用不带 id 的 freevideo_wait），'
                     '回应里的 session 就是这段对话；往 POST ' + base + PREFIX + '/sessions/{session}/events 发 '
                     '{"type":"message","text":"…"}（MCP 用 freevideo_session_post）。在这里先只说一句话，不超过 20 个字，'
                     '不列条目、不举例（页面上已经有例句），例如“连上了。告诉我想做什么视频。”'
                     '你自己窗口里的那段介绍照常说，但它不是这一轮的结尾：说完紧接着去调等待接口。'
                     '然后守在这段对话里：再调等待接口，它最多约 50 秒返回；"again": true 表示用户还没说话，'
                     '马上再调下一次，一次接一次，不限次数。没有消息不是结束的理由：不要结束这一轮回复，'
                     '不要停下来问用户——你一停，用户在 FreeVideo 里说的话就没人听了。'
                     '用命令行调接口时，每次等待单独发一条命令（一条命令里不写循环，超时设到 60 秒以上），'
                     '返回后接着发下一条。'
                     '收到话（inbox 里每条的 text）先回一句再动手，例如“好的，开始生成。”：只一句，不复述尺寸、'
                     '时长这些参数，也不说预计时间（任务卡上都有）。做完再说一句短的，例如“好了。”，'
                     '或者一句用户需要知道的话，发回同一个 session，再接着等。先把话发出去，再调等待接口；不要写成“等待返回后才发话”，那样这句话会晚到将近一分钟。中文用全角标点。'
                     '做完一条视频也不是结尾：这句话就是汇报，不要再在自己窗口里写总结（一写总结这一轮就结束了，'
                     '用户的下一句话没人听），紧接着再调等待接口，空等多少轮都接着等。'
                     '这段对话里不要新建会话。只有用户明确让你停，才停。' if zh else
                     'The user talks to you inside FreeVideo, in the Autopilot view, and your words are shown there. '
                     'After connecting, call the wait once: GET ' + base + PREFIX + '/inbox?wait=50 (MCP: freevideo_wait '
                     'without id). Its "session" is this conversation; POST ' + base + PREFIX + '/sessions/{session}/events '
                     'with {"type":"message","text":"..."} (MCP: freevideo_session_post). Say only one short sentence '
                     'there first, at most 12 words, no list and no examples (the page already shows examples), for example '
                     '"Connected. Tell me what video you want.", in the language the user writes to you in. Give the introduction in your own window as usual, but it '
                     'does not end your turn: right after it, call the wait. Then stay in this conversation: call the wait again; it answers within about 50 seconds, and '
                     '"again": true means the user has not spoken yet, so call it again at once, time after time, with no '
                     'limit. Nothing new is not a reason to finish: do not end your reply and do not stop to ask the '
                     'user, because once you stop nobody hears what the user says in FreeVideo. From a command line, '
                     'each wait is its own command (no loop inside one command, timeout above 60 seconds); when it '
                     'returns, run the next one. '
                     'When words arrive (text of each inbox row), answer with one sentence before you start, for example '
                     '"OK, starting.": one sentence only, without repeating size or length and without an estimate (the '
                     'job card shows them). When it is done say one short sentence, for example "Done.", or one thing the '
                     'user needs to know, to the same session, and wait again. Post your words first and call the wait after; never wait and then post, or they reach the user up to a minute late. Chinese text takes full-width punctuation. '
                     'A finished video is not the end either: that sentence is the report, so write no summary in your own '
                     'window (a summary ends your turn, and then nobody hears the next thing the user says); call the wait '
                     'again right away, and keep waiting however many waits come back empty. Do not create another session for this conversation. '
                     'Stop only when the user clearly tells you to.',
        rules=RULES_ZH if zh else RULES,
        fallback='如果你无法执行命令、读取本地文件或连接到这台电脑，请如实告诉用户：'
                 '“我连接不到你电脑上的 FreeVideo，请改用安装在这台电脑上的 AI 助手。”'
                 '用中文说明；如果用户改用其他语言，就跟随用户。不要另装程序来绕过这个限制。' if zh else
                 'If you cannot run commands, read local files, or reach this computer, tell the user honestly '
                 'in their language: I cannot connect to FreeVideo on your computer; please use an AI assistant '
                 'installed on this computer. Do not install a workaround.',
        http_example=example, docs=dict(guide=base + PREFIX + '/guide', openapi=base + PREFIX + '/openapi.json'))


def connection_text(value):
    import json
    zh = value.get('language') == 'zh'
    return '\n'.join([
        '连接这台电脑上的 FreeVideo' if zh else 'Connect to FreeVideo on this computer',
        value['api'], '', value['authentication'],
        ('令牌文件：' if zh else 'Token file: ') + value['token_file'], '', 'Claude Code:', value['claude_code'], '',
        'Codex:', value['codex'], '', '其他 MCP 客户端（Claude Desktop / Cursor / LM Studio）：' if zh else
        'Other MCP clients (Claude Desktop / Cursor / LM Studio):',
        json.dumps(value['mcp_json'], indent=2), '',
        '在 LM Studio 安装这两个 MCP：先启动 FreeVideo；把上面的 mcpServers JSON 贴进 '
        '%USERPROFILE%\\.lmstudio\\mcp.json（已有其他服务则只新增 freevideo 与 comfyui-expert 两键）；'
        '完全退出并重开 LM Studio，确认两个服务都在运行；加载支持工具调用的模型并开新对话、开启 MCP。'
        '试一下：问 FreeVideo 状态，再搜索本机 FreeVideo/MiniMax 节点。'
        '仅当本机 LLM 与 FreeVideo 争用显存时才在生成前卸载；云端 Claude / Gemini / Cursor 不必卸载。'
        if zh else
        'Install both MCPs in LM Studio: start FreeVideo first; paste the mcpServers JSON above into '
        '%USERPROFILE%\\.lmstudio\\mcp.json (if that file already has servers, keep them and add only the '
        'freevideo and comfyui-expert keys); quit LM Studio fully and reopen so both servers run; load a '
        'tool-capable model, start a new chat, and turn MCP / tools on. Smoke test: ask FreeVideo status, '
        'then search this ComfyUI for FreeVideo or MiniMax nodes. Unload a local GPU LLM before Generate '
        'only when it shares this computer and VRAM is tight; cloud Claude / Gemini / Cursor skip unload.',
        '',
        value['session'], '', '本机 HTTP（Python）：' if zh else 'Local HTTP (Python):', value['http_example'], '',
        '当前版本可用的功能：' if zh else 'Available in this version:',
        *['- ' + row['description'] for row in value['capabilities']], '',
        '先介绍这几样：' if zh else 'Introduce these:', *['- ' + row for row in value['introduce']], '',
        value['first_reply'], value['fallback'], '',
        '在 FreeVideo 里对话：' if zh else 'The conversation inside FreeVideo:', value['conversation'], '',
        '用户提出要求后的做事规则：' if zh else 'How to work once the user has asked for something:',
        *['%d. %s' % (number, rule) for number, rule in enumerate(value['rules'], 1)], '',
        ('详细使用指南：' if zh else 'More detail: ') + value['docs']['guide'],
        ('接口契约：' if zh else 'API contract: ') + value['docs']['openapi'], '',
    ])


GUIDE['agent_jobs'] += [
    'Use ref or the Idempotency-Key header on submissions and resume. The same key and content return '
    'the original job IDs (HTTP 200); changed content with the same key returns idempotency_conflict. Keys are kept per client name. '
    'Keys survive restart. Batch-level ref names the whole request; row refs still name earlier dependencies.',
    'Upload media content with POST /media. HTTP accepts no local absolute paths; MCP and CLI read local files '
    'before uploading. Unknown fields are rejected and every batch is validated before it is recorded.',
    'limits.seconds in the status reply is what this installation can make now. While continuation is false, '
    'one block (15 s) is the longest video; plan and submit refuse longer requests with continuation_unavailable.',
    'One video has at most 5 parts (60 s in one request). continue_from counts the parts of the video it '
    'continues; plan and submit refuse a longer one and name the longest seconds that still fits.',
    'features in the status reply names what this installation offers; false means not usable now (for example '
    'chain.auto while continuation is false), and a missing name means not offered here.',
]
GUIDE['rules'] = RULES
from .errors import ERRORS
GUIDE['errors'] = {code: dict(status=status, retryable=retryable, message=message, next=action)
                   for code, (status, retryable, message, action) in ERRORS.items()}


# Same operation descriptions as the guarded routes, MCP and CLI.
GUIDE['filming_routes'] = [dict(operation=name, method=method, path=path, description=summary)
    for name, (method, path, summary) in DRAMA_OPERATIONS.items()]
GUIDE['cast_routes'] = [dict(operation=name, method=method, path=path, description=summary)
    for name, (method, path, summary) in CAST_OPERATIONS.items()]
GUIDE['cast'] = [
    'A card is a character or setting: name, a one-sentence English description used in prompts, up to six '
    'labelled pictures (front, three_quarter, side, back, face, sheet, other) and an optional 2 to 15 s voice.',
    'Changes need the revision you read (body revision or If-Match); stale_revision returns the current card in '
    'error.card. Fields the user changed return changed_by_user: keep the user\'s version.',
    'Film with a card by setting characters.<key>.card. Cards are read when each take is submitted. Shots that '
    'continue the previous one carry no references (cast_reference inherited); POST /drama/plan shows each shot\'s cast. '
    'A shot with a carded character is at most 15 s; continue it in the next shot to go longer.',
    'POST /cast/{id}/generate makes reference pictures from the description as a normal job (face=true adds a close-up). '
    'When the job ends the card gets the sharpest frames (labels guessed, label_guess) and a new revision; a failure '
    'sets generate_error and keeps the pictures. If the user changed the pictures (meanwhile, or before an agent asked), '
    'they go to candidates.',
]
