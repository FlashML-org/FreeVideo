// Bundled tutorial copy, a short form of docs/zh-CN/使用说明.md; keep them consistent.
// Only finished chapters are listed. Add a chapter here when it is written, with its picture row in scripts/tutorial_pictures.json.
export const TUTORIAL_CHAPTERS = [
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
            zh: ['在创作面板的“素材”中添加图片，每张可设为参考、首帧或尾帧。', '在描述中输入 @ 选择图片，并写明它在画面中的作用，例如“<Picture 1> 里的女孩在海边奔跑”。', '视频和音频也可作为参考：点“上\u2060传\u2060文\u2060件”添加，在描述中输入 @ 引用。', '点“生成视频”。'],
            en: ['Add images under "Media" in the creation panel and set each one as a reference, first frame, or last frame.', 'Type @ in the prompt and choose an image, then say what it does in the scene, for example "The girl in <Picture 1> runs along the beach".', 'Videos and audio can be references too: add them with "Upload files" and mention them in the prompt with @.', 'Choose "Generate video".'],
        },
        limits: {
            zh: ['参考图生成仍在试验中，人物外观可能每次不同；清晰的正面图效果更稳定。', '首帧和尾帧可同时使用，会按画幅居中裁切；暂不能与参考图、参考视频同时使用。', '参考视频只取与成片等长的一段，最长 15 秒；生成用时约为只用图片时的两倍。'],
            en: ['Reference generation is experimental, so a character may look different between runs. A clearer, front-facing image helps.', 'First and last frames can be used together and are center-cropped to the frame shape. They cannot be used with reference images or videos yet.', 'A reference video is trimmed to the length of the result, up to 15 seconds, and generation takes about twice as long as with images only.'],
        },
    },
    {
        id: 'long-video',
        title: {zh: '连续的长视频', en: 'Make a longer video'},
        summary: {zh: '填写更长的时长自动分段生成，也可逐段接续。', en: 'Enter a longer duration to generate in parts, or continue one part at a time.'},
        media: {
            type: 'image',
            src: {zh: 'long-video-1.webp', en: 'long-video-2.webp'},
            alt: {zh: '创作面板：时长输入框和“可生成范围”入口已用红框标出。', en: 'The creation panel, with the duration field and "Available sizes and lengths" control outlined in red.'},
        },
        steps: {
            zh: ['填写更长的时长，会自动分段生成；“可生成范围”中可查看各尺寸的最长时长和预计用时。', '点“生成视频”。', '在生成好的视频上点“接着生成”，继续下一段。', '也可在“剪辑视图”中逐段接续。'],
            en: ['Enter a longer duration. It is made in parts automatically. "Available sizes and lengths" shows how long each size can be and about how long it takes.', 'Choose "Generate video".', 'Choose "Extend" on a finished video to continue it.', 'Parts can also be added one by one in "Edit".'],
        },
        limits: {
            zh: ['接着生成的每一段至少 5 秒。一个连续镜头最多 8 段，有短于 10 秒的段时最多 4 段；创作面板中时长最长 60 秒；分镜剧本可在剪辑视图中逐个镜头添加，或用 AI 全自动。', '分段生成和接着生成需要 NVIDIA 显卡或 Mac。', '段数越多，衔接处越容易出现变化；适合切换镜头处，可直接开始新的一段。'],
            en: ['Each continued part is at least 5 seconds. One continuous shot has at most 8 parts, or 4 when a part is shorter than 10 seconds, and the creation panel accepts up to 60 seconds. For a storyboard, add shots one by one in Edit, or use Autopilot.', 'Generating in parts and Extend need an NVIDIA graphics card or a Mac.', 'The more parts are joined, the more the joins may change. Where a cut fits, cut to a new shot and start a fresh part.'],
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
            zh: ['选中要接续的一段。', '点描述框旁的 +，上传或生成图片，最多 3 张。', '填写描述，然后点“生成”。'],
            en: ['Select the part to continue.', 'Click + next to the prompt to upload or generate images, up to 3.', 'Describe it, then choose "Generate".'],
        },
        limits: {
            zh: ['接续生成最多使用 3 张参考图，不使用首帧和尾帧。', '剪辑视图只能添加图片；参考视频和音频在创作面板的“素材”中添加。', '段数限制相同；需要 NVIDIA 显卡或 Mac。'],
            en: ['A part that continues the previous one uses up to 3 reference images and no first or last frames.', 'Edit takes images only. Add reference videos and audio under "Media" in the creation panel.', 'The same part limits apply. An NVIDIA graphics card or a Mac is required.'],
        },
    },
];
