// Bundled tutorial copy, a short form of docs/zh-CN/使用说明.md; keep them consistent.
// Only finished chapters are listed. Add a chapter here when it is written, with its picture row in scripts/tutorial_pictures.json.
export const TUTORIAL_CHAPTERS = [
    {
        id: 'quick-start',
        goto: ['prompt-box', 'generate-btn'],
        title: {zh: '做出第一条视频', en: 'Make your first video'},
        summary: {zh: '只写一句描述，其余使用默认设置。', en: 'Write one sentence and keep the default settings.'},
        media: {
            type: 'image',
            src: {zh: 'quick-start-1.webp', en: 'quick-start-2.webp'},
            alt: {zh: '创作面板：描述框和“生⁠成⁠视⁠频”按钮已用红框标出。', en: 'The creation panel, with the prompt box and the "Generate video" button outlined in red.'},
        },
        steps: {
            zh: ['打开“创作面板”，在“描述画面”中写一句话，说明谁在什么地方做什么。', '画幅选“横屏”或“竖屏”，其余保持默认。', '点“生⁠成⁠视⁠频”。', '完成后在预览区播放，点“下载视频”保存。'],
            en: ['Open "Create" and write one sentence under the prompt: who is where and doing what.', 'Choose "Landscape" or "Portrait" and keep the rest as it is.', 'Choose "Generate video".', 'When it finishes, play it in the preview and choose "Download video" to save it.'],
        },
        example: {
            assets: [],
            prompt: ['A small orange cat walks along a sunlit windowsill and looks out at the rain.', '一只橘色小猫沿着洒满阳光的窗台慢慢走过，望向窗外的雨。'],
            settings: {},
        },
        limits: {
            zh: ['首次生成需要加载模型，用时较长；之后会更快。', '每张显卡同一时间处理一个请求，其余请求在队列中等待。'],
            en: ['The first run loads the models and takes longer. Later runs are faster.', 'Each graphics card runs one request at a time, and the others wait in the queue.'],
        },
    },
    {
        id: 'text-to-video',
        goto: ['prompt-box'],
        title: {zh: '文字生成视频', en: 'Make a video from text'},
        summary: {zh: '用文字描述镜头，并选择画幅、清晰度和时长。', en: 'Describe the shot, then choose the shape, resolution and length.'},
        media: {
            type: 'image',
            src: {zh: 'text-to-video-1.webp', en: 'text-to-video-2.webp'},
            alt: {zh: '创作面板：描述、画幅与时长区域已用红框标出。', en: 'The creation panel, with the prompt and the Frame & duration fields outlined in red.'},
        },
        steps: {
            zh: ['在“描述画面”中写明主体、动作、场景和镜头，例如“镜头缓缓推近”。', '在“画幅与时长”中选择横屏、竖屏或方形，并设置“清晰度”。', '在“时长 · 秒”中填写秒数；“可⁠生⁠成⁠范⁠围”中可查看各尺寸的最长时长和预计用时。', '点“生⁠成⁠视⁠频”。描述为空时会先提示，点“继⁠续⁠生⁠成”仍可生成。'],
            en: ['Write the subject, action, place and camera in the prompt, for example "the camera slowly moves closer".', 'Under "Frame & duration", choose landscape, portrait or square and set "Resolution".', 'Enter the seconds under "Duration · seconds". "Available sizes and lengths" shows the longest length and the estimated time for each size.', 'Choose "Generate video". With an empty prompt, a note appears first; choose "Generate anyway" to go ahead.'],
        },
        example: {
            assets: [],
            prompt: ['A white stone castle rises above the mist as the camera slowly moves closer in daylight.', '白石城堡耸立在薄雾之上，日光下镜头缓缓推近。'],
            settings: {quality: 'High', width: 960, height: 544, seconds: 5},
        },
        limits: {
            zh: ['清晰度越高、时长越长，用时和显存占用越多。', '描述中的文字内容不一定能准确显示在画面里。', '开启“FreeToken 提示词增强”后，描述会在本机被改写得更详细。'],
            en: ['Higher resolution and longer length take more time and memory.', 'Text named in the prompt may not appear exactly in the picture.', 'With "FreeToken enhancement" on, the prompt is rewritten in more detail on this computer.'],
        },
    },
    {
        id: 'timed-lines',
        title: {zh: '按时间写台词和镜头', en: 'Write lines and cuts by time'},
        summary: {zh: '在描述中按时间写台词和切镜，镜头会在写定的时间切换。', en: 'Write lines and cuts with times in the prompt, and the shot changes at the times you set.'},
        media: {
            type: 'image',
            src: {zh: 'timed-lines-1.webp', en: 'timed-lines-2.webp'},
            alt: {zh: '创作面板：按时间书写台词和切镜的描述已用红框标出。', en: 'The creation panel, with a prompt of timed lines and a cut outlined in red.'},
        },
        steps: {
            zh: ['在描述中另起一行，以“分:秒”开头，例如“0:02”。', '时间后写“名字：台词”，表示这个人按顺序说这句话；直接写一句话，表示这时发生的事。', '时间后写“切：”再写新镜头的画面，镜头会在这个时间切换。'],
            en: ['Start a new line in the prompt with minutes:seconds, for example "0:02".', 'After the time, write "Name: line" for a line the person says in turn, or a plain sentence for something that happens.', 'After the time, write "Cut:" and the new shot, and the shot changes at that time.'],
        },
        example: {
            assets: [],
            prompt: ['A small platform at dawn. Lin hurries in with a bag; Jay stands by the bench.\n0:01 Jay: You\'re late.\n0:02 Lin: The train was delayed.\n0:04 Cut: The two walk side by side to the train, tracking from the side.', '清晨的小站台，小林提着包快步走来，阿杰站在长椅旁。\n0:01 阿杰：你迟到了。\n0:02 小林：车晚点了。\n0:04 切：两人并肩走向列车，侧面跟拍'],
            settings: {seconds: 8},
        },
        limits: {
            zh: ['时间必须在视频时长以内。', '没有写“切：”的地方不会切镜。'],
            en: ['Each time must be within the video length.', 'Without "Cut:", the shot does not change.'],
        },
    },
    {
        id: 'first-last-frame',
        title: {zh: '首帧和尾帧', en: 'First and last frames'},
        summary: {zh: '指定视频从哪张图开始、到哪张图结束。', en: 'Choose the image the video starts from and the one it ends on.'},
        media: {
            type: 'image',
            src: {zh: 'first-last-frame-1.webp', en: 'first-last-frame-2.webp'},
            alt: {zh: '创作面板：素材区域中图片的用途下拉框已用红框标出。', en: 'The creation panel, with the image role selectors in the Media section outlined in red.'},
        },
        steps: {
            zh: ['在“素材”中点“上传文件”，添加起始图片，并将它的用途下拉框选为“首帧”。', '需要结尾画面时，再添加一张图片并设为“尾⁠帧”。', '在描述中写明两张图之间发生了什么。', '点“生⁠成⁠视⁠频”。'],
            en: ['Under "Media", choose "Upload files", add the starting image and set its role dropdown to "First frame".', 'For an ending picture, add another image and set it to "Last frame".', 'Describe what happens between the two images.', 'Choose "Generate video".'],
        },
        example: {
            assets: [{file: 'anime-first.webp', role: 'first'}, {file: 'anime-last.webp', role: 'last'}],
            prompt: ['Anime style, a girl with long dark hair and a school uniform in a sunlit classroom raises her head slowly and turns to look back over her shoulder with a gentle smile, still camera.', '动漫风格，长发校服少女在阳光教室里缓缓抬头，回头看向镜头并微笑，镜头固定。'],
            settings: {},
        },
        limits: {
            zh: ['图片会按所选画幅居中裁切。', '首帧、尾帧不能与参考图、角色同时使用。'],
            en: ['Images are center-cropped to the chosen shape.', 'First and last frames cannot be used with reference images or characters.'],
        },
    },
    {
        id: 'reference-video',
        title: {zh: '参考图生成视频', en: 'Make a video from reference images'},
        summary: {zh: '让参考图里的人物、物品或场景出现在视频中。', en: 'Use people, objects or places from reference images in a video.'},
        media: {
            type: 'image',
            src: {zh: 'reference-video-1.webp', en: 'reference-video-2.webp'},
            alt: {zh: '创作面板：素材区域和参考图已用红框标出。', en: 'The creation panel, with the Media section and a reference image outlined in red.'},
        },
        steps: {
            zh: ['在创作面板的“素材”中添加图片，每张可设为参考、首帧或尾帧。', '在描述中输入 @ 选择图片，并写明它在画面中的作用，例如“<Picture 1> 里的女孩在海边奔跑”。', '视频和音频也可作为参考：点“上\u2060传\u2060文\u2060件”添加，在描述中输入 @ 引用。', '音频卡片上选“原样使用”时，成片直接使用这段声音，时长随音频而定；一次只能有一段音频原样使用。', '点“生⁠成⁠视⁠频”。'],
            en: ['Add images under "Media" in the creation panel and set each one as a reference, first frame, or last frame.', 'Type @ in the prompt and choose an image, then say what it does in the scene, for example "The girl in <Picture 1> runs along the beach".', 'Videos and audio can be references too: add them with "Upload files" and mention them in the prompt with @.', 'On an audio card, choose "Use as is" to put that audio into the result unchanged. The duration follows the audio, and only one audio file can be used as is.', 'Choose "Generate video".'],
        },
        example: {
            assets: [],
            prompt: ['A girl runs along the beach at sunset and waves at the camera.', '一个女孩在日落的海边奔跑，并向镜头挥手。'],
            settings: {},
        },
        limits: {
            zh: ['参考图生成仍在试验中，人物外观可能每次不同；清晰的正面图效果更稳定。', '首帧和尾帧可同时使用，会按画幅居中裁切；暂不能与参考图、参考视频同时使用。', '参考视频只取与成片等长的一段，最长 15 秒；生成用时约为只用图片时的两倍。'],
            en: ['Reference generation is experimental, so a character may look different between runs. A clearer, front-facing image helps.', 'First and last frames can be used together and are center-cropped to the frame shape. They cannot be used with reference images or videos yet.', 'A reference video is trimmed to the length of the result, up to 15 seconds, and generation takes about twice as long as with images only.'],
        },
    },
    {
        id: 'cast-sheet',
        goto: ['cast-add'],
        title: {zh: '角色库和角色设定图', en: 'Characters and character sheets'},
        summary: {zh: '把人物保存为角色，之后在描述中用 @ 引用。', en: 'Save a person as a character, then mention it with @ in later prompts.'},
        media: {
            type: 'image',
            src: {zh: 'cast-sheet-1.webp', en: 'cast-sheet-2.webp'},
            alt: {zh: '角色窗口：名字、正面图片和其他角度图片已用红框标出。', en: 'The character window, with the name, front image and other angles outlined in red.'},
        },
        steps: {
            zh: ['展开创作面板的“素材”，在“角色”一行点“+”新建角色。', '填写“名字”，设置“正面图片”：可“上⁠传⁠图⁠片”，也可“AI 生成”，后者会按描述一次生成 5 张供选。', '可再添加最多 5 张其他角度的图片，然后点“保⁠存”。', '之后在描述中输入 @ 选择该角色，并写明其动作。'],
            en: ['Expand "Media" in the creation panel and, on the "Characters" row, choose + to create a character.', 'Enter a "Name" and set the "Front image": choose "Upload images", or "Generate with AI", which makes 5 pictures from a description to pick from.', 'Add up to 5 images from other angles, then choose "Save".', 'In later prompts, type @ to pick the character and describe what the character does.'],
        },
        example: {
            assets: [{file: 'fox-sheet.webp', role: 'reference'}],
            prompt: ['Live-action nature film, the fox shown in {image1} runs through a snowy field with soft snowfall, the camera follows beside it.', '纪录片风格，{image1} 中的狐狸在飘雪的田野上奔跑，镜头在一侧跟随。'],
            settings: {},
        },
        limits: {
            zh: ['一次请求最多使用 3 个角色。', '角色不能与首帧、尾帧同时使用。', '角色的外观可能每次略有不同。'],
            en: ['A request uses up to 3 characters.', 'Characters cannot be used with first or last frames.', 'A character may look slightly different between runs.'],
        },
    },
    {
        id: 'replace-person',
        title: {zh: '替换人物', en: 'Replace person'},
        summary: {zh: '把视频里的人换成人物图中的人，动作和镜头沿用源视频。', en: 'Put the person from an image into a video, keeping the motion and camera of the source video.'},
        media: {
            type: 'image',
            src: {zh: 'replace-person-1.webp', en: 'replace-person-2.webp'},
            alt: {zh: '创作面板：素材中的“替换人物”、源视频和人物图两格、“保⁠留⁠源⁠视⁠频⁠声⁠音”已用红框标出。', en: 'The creation panel, with "Replace person", the source video and person image slots, and "Keep source video sound" outlined in red.'},
        },
        steps: {
            zh: ['展开创作面板的“素材”，打开“替换人物”。', '在“源视频”中点“添加视频”，在“人物图”中点“添加图片”。', '需要源视频的声音时，勾选“保⁠留⁠源⁠视⁠频⁠声⁠音”。', '描述可以留空，也可以补充画面要求；然后点“生⁠成⁠视⁠频”。'],
            en: ['Expand "Media" in the creation panel and turn on "Replace person".', 'Under "Source video" choose "Add video", and under "Person image" choose "Add image".', 'To keep the sound of the source video, check "Keep source video sound".', 'The prompt can stay empty or describe more of the picture. Then choose "Generate video".'],
        },
        limits: {
            zh: ['替换人物不能与首帧、尾帧同时使用。', '首次使用时会自动下载约 80 MB 的替换人物模型，面板中显示进度，下载完成后才能生成。', '源视频至少 1 秒；源视频没有声音时，不能保留声音。'],
            en: ['Replace person cannot be used with first or last frames.', 'The first time, the Replace person model of about 80 MB downloads on its own. The panel shows the progress, and generation can start when it finishes.', 'The source video must be at least 1 second long. A source video without sound has no sound to keep.'],
        },
    },
    {
        id: 'quality-tiers',
        title: {zh: '四个质量档位', en: 'The four quality levels'},
        summary: {zh: '档位越靠右，用时越长、细节越多。', en: 'Further right takes longer and keeps more detail.'},
        media: {
            type: 'image',
            src: {zh: 'quality-tiers-1.webp', en: 'quality-tiers-2.webp'},
            alt: {zh: '创作面板：质量滑块上的轻量、标准、精细、极致已用红框标出。', en: 'The creation panel, with the quality slider and its four names outlined in red.'},
        },
        steps: {
            zh: ['在“质量”滑块上选择“轻量”“标准”“精细”或“极⁠致”。', '构思阶段用“轻量”，快速确认画面和动作。', '定稿时改用“精细”或“极⁠致”。', '选好档位后，“质量”旁会显示预计用时；然后点“生⁠成⁠视⁠频”。'],
            en: ['Move the "Quality" slider to Light, Medium, High or Max.', 'While exploring, use Light to check the picture and motion quickly.', 'For the final video, switch to High or Max.', 'The estimated time for the chosen level appears next to "Quality". Then choose "Generate video".'],
        },
        example: {
            assets: [],
            prompt: ['A golden puppy runs across a sunny green meadow toward the camera, ears flopping, soft daylight.', '一只金毛小狗在阳光明媚的绿色草地上朝镜头跑来，耳朵晃动，柔和的日光。'],
            settings: {quality: 'High'},
        },
        limits: {
            zh: ['预计用时因显卡而异，以面板显示为准。', '不同档位各自生成，画面构图可能不同，不宜逐帧对比。'],
            en: ['Time depends on the graphics card; use the estimate shown in the panel.', 'Each level is generated on its own, so the composition may differ and the results should not be compared frame by frame.'],
        },
    },
    {
        id: 'refine',
        title: {zh: '先出预览，再继续二采', en: 'Preview first, then run the second pass'},
        summary: {zh: '先出半分辨率预览，满意后再生成完整视频。', en: 'Make a half-resolution preview first, then the full video when it looks right.'},
        media: {
            type: 'image',
            src: {zh: 'refine-1.webp', en: 'refine-2.webp'},
            alt: {zh: '创作面板：二次采样选项“先出预览”已用红框标出。', en: 'The creation panel, with the "Preview first" option outlined in red.'},
        },
        steps: {
            zh: ['在二次采样中选择“先出预览”，按钮变为“生⁠成⁠预⁠览”。', '点“生⁠成⁠预⁠览”，等待预览完成。', '画面满意时，点结果中的“继续二采”。', '不满意时，修改描述或种子，再生成新的预览。'],
            en: ['Under two-pass sampling choose "Preview first". The button changes to "Generate preview".', 'Choose "Generate preview" and wait for it to finish.', 'When the picture is right, choose "Run second pass" in the result.', 'Otherwise change the prompt or the seed and generate a new preview.'],
        },
        example: {
            assets: [],
            prompt: ['A dark castle emerges from the mist as the camera slowly moves closer.', '深色城堡从薄雾中显现，镜头缓缓推近。'],
            settings: {two_pass: true, preview: true},
        },
        limits: {
            zh: ['完整视频与直接生成的结果一致。', '预览生成较快，但仍需加载模型。'],
            en: ['The full video matches a direct run.', 'A preview is quicker, but the models still have to load.'],
        },
    },
    {
        id: 'long-video',
        title: {zh: '连续的长视频', en: 'Make a longer video'},
        summary: {zh: '填写更长的时长自动分段生成，也可逐段接续。', en: 'Enter a longer duration to generate in parts, or continue one part at a time.'},
        media: {
            type: 'image',
            src: {zh: 'long-video-1.webp', en: 'long-video-2.webp'},
            alt: {zh: '创作面板：时长输入框和“可⁠生⁠成⁠范⁠围”入口已用红框标出。', en: 'The creation panel, with the duration field and "Available sizes and lengths" control outlined in red.'},
        },
        steps: {
            zh: ['填写更长的时长，会自动分段生成；“可⁠生⁠成⁠范⁠围”中可查看各尺寸的最长时长和预计用时。', '点“生⁠成⁠视⁠频”。', '在生成好的视频下方点“接着生成”，继续下一段。', '更长的多镜头视频，可在“剪辑视图”中另起新的镜头，或使用“AI 全自动”。'],
            en: ['Enter a longer duration. It is made in parts automatically. "Available sizes and lengths" shows how long each size can be and about how long it takes.', 'Choose "Generate video".', 'Choose "Extend" below a finished video to continue it.', 'For a longer video with several shots, start new shots in "Edit", or use "Autopilot".'],
        },
        example: {
            assets: [],
            prompt: ['A hiker crosses a mountain ridge at dawn. The camera follows from behind, then rises to show the valley below.', '清晨，一名徒步者走过山脊，镜头在身后跟随，随后升起，展现脚下的山谷。'],
            settings: {},
        },
        limits: {
            zh: ['接着生成的每一段至少 5 秒，最后一段可短至 2 秒。一个连续镜头最多 8 段，有短于 10 秒的段时最多 4 段；创作面板中时长最长 60 秒；分镜剧本可在剪辑视图中逐个镜头添加，或用 AI 全自动。', '分段生成和接着生成需要 NVIDIA 显卡或 Mac。', '段数越多，衔接处越容易出现变化；适合切换镜头处，可直接开始新的一段。'],
            en: ['Each continued part is at least 5 seconds, and the last one can be as short as 2 seconds. One continuous shot has at most 8 parts, or 4 when a part is shorter than 10 seconds, and the creation panel accepts up to 60 seconds. For a storyboard, add shots one by one in Edit, or use Autopilot.', 'Generating in parts and Extend need an NVIDIA graphics card or a Mac.', 'The more parts are joined, the more the joins may change. Where a cut fits, cut to a new shot and start a fresh part.'],
        },
    },
    {
        id: 'extend-video',
        title: {zh: '延长已有视频', en: 'Extend an existing video'},
        summary: {zh: '从一条已有视频的结尾继续往下生成。', en: 'Continue from the end of a video you already have.'},
        media: {
            type: 'image',
            src: {zh: 'extend-video-1.webp', en: 'extend-video-2.webp'},
            alt: {zh: '剪辑视图：接着生成的草稿段和描述框已用红框标出。', en: 'The Edit view, with the draft part that continues the video and the prompt box outlined in red.'},
        },
        steps: {
            zh: ['在生成好的视频下方点“接着生成”，打开剪辑视图。', '选中后面自动加上的那一段，在底部描述框写明接下来发生什么。这一段默认 10 秒，在 Mac 和一次生成不了 10 秒的设备上为 5 秒。', '需要更长时，拖长这一段，每段至少 5 秒，然后点“生⁠成”。', '新的一段会接在原视频之后，并自动拼成一条。'],
            en: ['Choose "Extend" below a finished video. The Edit view opens.', 'Select the part added after it and write what happens next in the box at the bottom. The part is 10 seconds by default, or 5 seconds on a Mac and on devices that cannot make 10 seconds at once.', 'To make it longer, drag the part out; each part is at least 5 seconds. Then choose "Generate".', 'The new part follows the original and is joined into one video.'],
        },
        example: {
            assets: [],
            prompt: ['The cat jumps down from the windowsill and trots across the wooden floor toward the kitchen.', '小猫从窗台跳下，沿着木地板小跑向厨房。'],
            settings: {},
        },
        limits: {
            zh: ['需要 NVIDIA 显卡或 Mac。'],
            en: ['An NVIDIA graphics card or a Mac is required.'],
        },
    },
    {
        id: 'continue-or-new-shot',
        title: {zh: '接着生成，还是另起镜头', en: 'Continue a shot or start a new one'},
        summary: {zh: '接着生成是同一个镜头的下一段；在时间线空白处另起的是新镜头。', en: 'Extend makes the next part of the same shot. A part dragged out in an empty place on the timeline is a new shot.'},
        media: {
            type: 'image',
            src: {zh: 'continue-or-new-shot-1.webp', en: 'continue-or-new-shot-2.webp'},
            alt: {zh: '剪辑视图时间线：接续上一段的草稿带有连接标记，与后面空开的独立镜头已用红框标出。', en: 'The Edit timeline, with the draft that continues the video, marked by a link, and the separate shot after a gap outlined in red.'},
        },
        steps: {
            zh: ['要让画面接着上一段往下发展，在生成好的视频下方点“接着生成”：新的一段紧接在后面，带有连接标记，与前面拼成同一个镜头。', '要换场景或切到另一个画面，在时间线的空白处拖出一段，写下描述再点“生⁠成”：它是独立镜头，不接前面的画面。', '看时间线：接续的一段紧贴前一段，带连接标记；独立镜头与前面的视频分开显示。'],
            en: ['To carry the picture on from the previous part, choose "Extend" below a finished video. The new part follows right after it, carries a link mark, and joins the earlier part as one shot.', 'To change scene or cut to another picture, drag out a part in an empty place on the timeline, describe it and choose "Generate". It is a separate shot and does not continue the earlier picture.', 'Check the timeline: a continuing part sits right against the part before it and carries a link mark. A separate shot is drawn apart from the video before it.'],
        },
        example: {
            assets: [],
            prompt: ['Inside a quiet cafe, rain runs down the window while a barista wipes the counter.', '安静的咖啡馆里，雨水顺着窗户流下，店员擦拭着柜台。'],
            settings: {},
        },
        limits: {
            zh: ['同一个连续镜头最多 8 段，有短于 10 秒的段时最多 4 段；另起的独立镜头不计入。', '接着生成和另起镜头都需要 NVIDIA 显卡或 Mac。'],
            en: ['One continuous shot has at most 8 parts, or 4 when a part is shorter than 10 seconds. A separate shot does not count toward them.', 'Extend and new shots both need an NVIDIA graphics card or a Mac.'],
        },
    },
    {
        id: 'editor',
        goto: ['editor-timeline'],
        title: {zh: '剪辑视图：裁剪、描述、接续', en: 'The Edit view: crop, describe, continue'},
        summary: {zh: '在时间线上选中一段，描述它，并接着生成。', en: 'Select a part on the timeline, describe it and continue.'},
        media: {
            type: 'image',
            src: {zh: 'editor-1.webp', en: 'editor-2.webp'},
            alt: {zh: '剪辑视图：选中的一段和描述框已用红框标出。', en: 'The Edit view, with the selected part and the prompt box outlined in red.'},
        },
        steps: {
            zh: ['点顶部的“剪辑视图”。', '在时间线上选中要处理的一段。', '在下方描述框中填写这一段的内容，点“生⁠成”接续下一段。', '在右侧“画面”中可调整缩放、位置和旋转，或点“裁⁠剪”；窗口窄于 1280 时，先点“调⁠整”打开该面板。', '完成后点右上角的“导⁠出”保存。'],
            en: ['Choose "Edit" at the top.', 'Select a part on the timeline.', 'Write what happens in this part in the box below and choose "Generate" to continue.', 'Under "Video" on the right, adjust scale, position and rotation, or choose "Crop". On a window narrower than 1280 pixels, choose "Adjust" first to open the panel.', 'When done, choose "Export" at the top right.'],
        },
        example: {
            assets: [],
            prompt: ['The runner reaches the top of the hill, stops, and looks out over the valley as the sun comes up.', '跑步的人登上山顶，停下脚步，望着山谷中升起的太阳。'],
            settings: {},
        },
        limits: {
            zh: ['同一个连续镜头最多 8 段，有短于 10 秒的段时最多 4 段；在时间线后面另起的新镜头不计入。', '接续需要 NVIDIA 显卡或 Mac。'],
            en: ['One continuous shot has at most 8 parts, or 4 when a part is shorter than 10 seconds; a new shot started later on the timeline does not count.', 'Continuing needs an NVIDIA graphics card or a Mac.'],
        },
    },
    {
        id: 'editor-extend-refs',
        title: {zh: '在剪辑视图延长时加参考图', en: 'Add reference images when extending in Edit'},
        summary: {zh: '用参考图引导接着生成的那一段。', en: 'Use reference images to guide the next part.'},
        media: {
            type: 'image',
            src: {zh: 'editor-extend-refs-1.webp', en: 'editor-extend-refs-2.webp'},
            alt: {zh: '剪辑视图：选中的接续段和描述框旁的参考图入口已用红框标出。', en: 'The Edit view, with the selected continuation and the reference image control beside the prompt outlined in red.'},
        },
        steps: {
            zh: ['选中要接续的一段。', '点描述框旁的 +，上传或生成图片，最多 3 张。', '填写描述，然后点“生⁠成”。'],
            en: ['Select the part to continue.', 'Click + next to the prompt to upload or generate images, up to 3.', 'Describe it, then choose "Generate".'],
        },
        example: {
            assets: [],
            prompt: ['The traveler steps into the market square. Lanterns glow above the stalls.', '旅人走进集市广场，摊位上方灯笼亮着。'],
            settings: {},
        },
        limits: {
            zh: ['接续生成最多使用 3 张参考图，不使用首帧和尾帧。', '剪辑视图只能添加图片；参考视频和音频在创作面板的“素材”中添加。', '段数限制相同；需要 NVIDIA 显卡或 Mac。'],
            en: ['A part that continues the previous one uses up to 3 reference images and no first or last frames.', 'Edit takes images only. Add reference videos and audio under "Media" in the creation panel.', 'The same part limits apply. An NVIDIA graphics card or a Mac is required.'],
        },
    },
    {
        id: 'segment-prompt',
        goto: ['segment-prompt'],
        title: {zh: '逐段描述', en: 'Describe each part'},
        summary: {zh: '为剪辑视图中的每一段单独写描述。', en: 'Write a separate description for each part in the Edit view.'},
        media: {
            type: 'image',
            src: {zh: 'segment-prompt-1.webp', en: 'segment-prompt-2.webp'},
            alt: {zh: '剪辑视图：选中的一段和描述框已用红框标出。', en: 'The Edit view, with the selected part and the prompt box outlined in red.'},
        },
        steps: {
            zh: ['在“画面”轨上拖出一段较长的范围，它会分成带序号的多段。', '点选其中一段，在描述框中写下这一段的内容。', '再点选另一段，写下它的内容；每段的描述保存在这一段上。', '点“生⁠成”，各段按顺序生成并连成一个视频。'],
            en: ['On the picture track, drag a long range. It is divided into numbered parts.', 'Select one part and write what happens in it in the prompt box.', 'Select another part and write its own description. Each part keeps its own.', 'Choose "Generate". The parts are made in order and joined into one video.'],
        },
        limits: {
            zh: ['一个连续镜头的段数有上限，见“连续的长视频”。'],
            en: ['One continuous shot has a limit on the number of parts; see "Make a longer video".'],
        },
    },
    {
        id: 'separate-tracks',
        goto: ['audio-track-button'],
        title: {zh: '分离音轨和替换声音', en: 'Separate tracks and replace sound'},
        summary: {zh: '把声音分成人声、音乐和环境声，并分别替换。', en: 'Split the sound into dialogue, music and ambience and replace each one.'},
        media: {
            type: 'image',
            src: {zh: 'separate-tracks-1.webp', en: 'separate-tracks-2.webp'},
            alt: {zh: '剪辑视图：选中的声音片段和“分⁠离⁠音⁠轨”按钮已用红框标出。', en: 'The Edit view, with a selected sound clip and the "Separate tracks" button outlined in red.'},
        },
        steps: {
            zh: ['在剪辑视图中选中带声音的片段。', '点“分⁠离⁠音⁠轨”，人声、音乐和环境声会分成三条轨道。', '选中“音乐”轨上的片段，在描述框中写下想要的音乐，点“替⁠换⁠配⁠乐”。', '选中“人声”轨上的片段，在“台词”中修改某一句的文字。', '点“重⁠新⁠配⁠音”，只重配改过的句子。'],
            en: ['Select a clip with sound in the Edit view.', 'Choose "Separate tracks". Dialogue, music and ambience become three tracks.', 'Select a clip on the music track, describe the music you want and choose "Replace music".', 'Select a clip on the dialogue track and change the words of one line under "Lines".', 'Choose "Redub". The changed lines are spoken again.'],
        },
        limits: {
            zh: ['部分电脑暂不支持分离音轨，此时按钮不会出现。'],
            en: ['Some computers cannot separate tracks. The button does not appear on them.'],
        },
    },
    {
        id: 'replace-music',
        goto: ['audio-a2-track'],
        title: {zh: '加配乐', en: 'Add music'},
        summary: {zh: '在声音轨上选一段，描述想要的音乐。', en: 'Select a range on a sound track and describe the music.'},
        media: {
            type: 'image',
            src: {zh: 'replace-music-1.webp', en: 'replace-music-2.webp'},
            alt: {zh: '剪辑视图：第二条声音轨上选中的一段和风格选项已用红框标出。', en: 'The Edit view, with a range on the second sound track and the style options outlined in red.'},
        },
        steps: {
            zh: ['点“＋ 声音轨”，添加第二条声音轨“声音 2”。', '在“声音 2”轨的空白处拖出一段。', '在“风格”和“情绪”中各选一项。', '在“描述配乐”中写下想要的音乐。', '点“生⁠成”。'],
            en: ['Choose "+ Audio track" to add a second audio track, "Audio 2".', 'Drag a range on an empty part of the "Audio 2" track.', 'Choose one option under "Style" and one under "Mood".', 'Describe the music under "Describe the music".', 'Choose "Generate".'],
        },
        limits: {
            zh: ['首次生成需要下载配乐模型。', '要换掉分离后的音乐，请选中“音乐”轨上的片段，按钮会变为“替⁠换⁠配⁠乐”。'],
            en: ['The first run downloads the music model.', 'To replace separated music, select a clip on the music track. The button then reads "Replace music".'],
        },
    },
    {
        id: 'voice-recording',
        goto: ['record-entry'],
        title: {zh: '配音与录音', en: 'Voice-over and recording'},
        summary: {zh: '直接录音，或对已有的台词重新配音。', en: 'Record your own voice, or redub lines that already exist.'},
        media: {
            type: 'image',
            src: {zh: 'voice-recording-1.webp', en: 'voice-recording-2.webp'},
            alt: {zh: '剪辑视图：工具栏中的“录音”按钮已用红框标出。', en: 'The Edit view, with the "Record" button in the toolbar outlined in red.'},
        },
        steps: {
            zh: ['点工具栏中的“录⁠音”。', '点“开⁠始⁠录⁠音”，对着麦克风说话。', '点“停⁠止”结束，录音会放到声音轨上。', '已有的人声可逐句改写：选中“人声”轨上的片段，修改“台词”中的文字，点“重⁠新⁠配⁠音”。'],
            en: ['Choose "Record" in the toolbar.', 'Choose "Start recording" and speak into the microphone.', 'Choose "Stop". The recording is placed on an audio track.', 'Existing dialogue can be changed line by line: select a clip on the dialogue track, edit the words under "Lines" and choose "Redub".'],
        },
        limits: {
            zh: ['录音需要允许使用麦克风。'],
            en: ['Recording needs permission to use the microphone.'],
        },
    },
    {
        id: 'sound-to-picture',
        goto: ['editor-timeline'],
        title: {zh: '按声音生成画面', en: 'Generate a picture from sound'},
        summary: {zh: '为只有声音的部分生成匹配的画面。', en: 'Generate a picture that matches a part that only has sound.'},
        media: {
            type: 'image',
            src: {zh: 'sound-to-picture-1.webp', en: 'sound-to-picture-2.webp'},
            alt: {zh: '剪辑视图：画面轨上选中的一段和描述框已用红框标出。', en: 'The Edit view, with a range on the picture track and the prompt box outlined in red.'},
        },
        steps: {
            zh: ['在剪辑视图中放入一段只有声音的素材。', '在“画面”轨上，对着声音拖出一段。', '在“描述这一段”中写下想看到的画面。', '点“生⁠成”。'],
            en: ['Put a clip that only has sound into the Edit view.', 'On the picture track, drag a range along the sound.', 'Describe the picture you want under "Describe this part".', 'Choose "Generate".'],
        },
        limits: {
            zh: ['画面时长以所选的一段为准。'],
            en: ['The picture has the length of the selected range.'],
        },
    },
    {
        id: 'autopilot',
        title: {zh: 'AI 全自动', en: 'Autopilot'},
        summary: {zh: '用对话说明想拍的内容，由 AI 分镜头并逐个生成。', en: 'Say what to film in a chat; the AI plans the shots and makes them one by one.'},
        media: {
            type: 'image',
            src: {zh: 'autopilot-1.webp', en: 'autopilot-2.webp'},
            alt: {zh: 'AI 全自动：连接 Agent 的按钮已用红框标出。', en: 'Autopilot, with the button that connects an agent outlined in red.'},
        },
        steps: {
            zh: ['点顶部的“AI 全自动”。', '点“连接 Agent”卡片中的按钮复制连接信息，粘贴给所使用的 Agent。', '状态显示“已连接”或所连 Agent 的名字后，在“对 Agent 说…”中描述想做的视频，例如“做一条 1 分钟的竖屏短片：咖啡店的一天”。', '点“发送”，Agent 会生成并剪辑；可随时点“停止”，完成后点“打开”或“剪辑”。', '在对话列表中点对话旁的“更多”，选“重命名”可修改对话名称。'],
            en: ['Choose "Autopilot" at the top.', 'On the "Connect an agent" card, choose the button to copy the connection details and paste them to the agent you use.', 'When it shows "Connected" or the agent\'s name, describe the video under "Message the agent…", for example "Make a 1-minute vertical short film: a day in a coffee shop".', 'Choose "Send". The agent generates and edits it. Choose "Stop" at any time, and "Open" or "Edit" when it finishes.', 'In the chat list, choose "More" next to a chat, then "Rename" to change its name.'],
        },
        limits: {
            zh: ['需要先连接 Agent，未连接时只显示连接说明。', '镜头越多，用时越长。', 'Agent 开拍整部片前、重拍耗时较长时是否先询问，可在“设置”的“AI Agent”中选择。'],
            en: ['An agent must be connected first. Until then only the connection card is shown.', 'More shots take longer.', 'Whether an agent asks before starting a film or a long reshoot is set under "AI Agent" in Settings.'],
        },
    },
    {
        id: 'library-reuse',
        goto: ['library-reuse', 'works-entry'],
        title: {zh: '复用设置', en: 'Reuse settings'},
        summary: {zh: '把以前一条视频的描述、尺寸、时长和种子填回创作面板。', en: 'Fill the prompt, size, duration and seed of an earlier video back into the creation panel.'},
        media: {
            type: 'image',
            src: {zh: 'library-reuse-1.webp', en: 'library-reuse-2.webp'},
            alt: {zh: '预览区下方的按钮：“复用设置”已用红框标出。', en: 'The buttons below the preview, with "Reuse settings" outlined in red.'},
        },
        steps: {
            zh: ['点预览区右上角“作品”左边的图标，选一条以前的视频。', '点“复⁠用⁠设⁠置”，描述、尺寸、时长和种子会填回创作面板。', '改动需要变化的内容，再点“生⁠成⁠视⁠频”。', '填入有误时，10 秒内点“撤⁠销”，恢复原来的内容。'],
            en: ['Choose the icon to the left of "Creations" above the preview and pick an earlier video.', 'Choose "Reuse settings". Its prompt, size, duration and seed are filled into the creation panel.', 'Change what should be different, then choose "Generate video".', 'If the fill was a mistake, choose "Undo" within 10 seconds to bring back what was there.'],
        },
        limits: {
            zh: ['没有保存描述的视频不提供“复用设置”。'],
            en: ['A video that kept no prompt does not offer "Reuse settings".'],
        },
    },
    {
        id: 'library-export',
        goto: ['works-entry'],
        title: {zh: '作品库和导出', en: 'Creations and export'},
        summary: {zh: '查找生成过的视频，并导出为文件。', en: 'Find earlier videos and export them as files.'},
        media: {
            type: 'image',
            src: {zh: 'library-export-1.webp', en: 'library-export-2.webp'},
            alt: {zh: '“我的作品”：第一条作品已用红框标出。', en: 'Your creations, with the first video outlined in red.'},
        },
        steps: {
            zh: ['点预览区的“作品”，打开“我的作品”。', '选择一条视频，可播放，或点“接着生成”在剪辑视图中继续。', '在作品详情中点爱心图标收藏；列表上方选“收⁠藏”只看收藏的作品，选“全⁠部”看所有作品。', '需要成片时，在“剪辑视图”点“导⁠出”。', '在导出窗口选择范围、画质和内容，再点“导⁠出”。'],
            en: ['Choose "Creations" in the preview area to open the library.', 'Pick a video to play it, or choose "Extend" to continue it in the Edit view.', 'In a creation\'s details, choose the heart to add it to favorites. Above the list, choose "Favorites" to see only those, or "All" to see every creation.', 'For a finished file, choose "Export" in the Edit view.', 'In the export window choose the range, quality and content, then choose "Export".'],
        },
        limits: {
            zh: ['删除作品前会确认；被剪辑使用的作品不能删除。', '导出过程中取消不会留下不完整的文件。'],
            en: ['FreeVideo asks before deleting a video, and a video used in an edit cannot be deleted.', 'Cancelling an export leaves no unfinished file.'],
        },
    },
    {
        id: 'troubleshoot',
        title: {zh: '出错了怎么办', en: 'When something goes wrong'},
        summary: {zh: '读取提示、保存诊断报告。', en: 'Read the message and save a diagnostic report.'},
        media: {
            type: 'image',
            src: {zh: 'troubleshoot-1.webp', en: 'troubleshoot-2.webp'},
            alt: {zh: '错误提示：“导出报告”和“提交问题”按钮已用红框标出。', en: 'An error message, with the "Export report" and "Report a problem" buttons outlined in red.'},
        },
        steps: {
            zh: ['先阅读错误提示，其中已写明可尝试的操作。', '仍然失败时，点“导出报告”保存诊断信息。', '点“提交问题”，将报告附到 GitHub issue。', '提示需要重启时，重新启动 FreeVideo。'],
            en: ['Read the error message first. It names what to try.', 'If it still fails, choose "Export report" to save the diagnostic information.', 'Choose "Report a problem" and attach the report to a GitHub issue.', 'When asked to restart, restart FreeVideo.'],
        },
        limits: {
            zh: ['报告中的路径已做脱敏处理。', '全部下载源都失败时，错误卡片会逐个列出下载源和失败原因。'],
            en: ['Paths in the report are anonymized.', 'When every download source fails, the error card lists each source and the reason it failed.'],
        },
    },
];
