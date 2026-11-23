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
        summary: {zh: '用文字描述镜头，并选择画幅、尺寸和时长。', en: 'Describe the shot, then choose the shape, size and length.'},
        media: {
            type: 'image',
            src: {zh: 'text-to-video-1.webp', en: 'text-to-video-2.webp'},
            alt: {zh: '创作面板：描述、画幅与尺寸区域已用红框标出。', en: 'The creation panel, with the prompt and the Frame & size fields outlined in red.'},
        },
        steps: {
            zh: ['在“描述画面”中写明主体、动作、场景和镜头，例如“镜头缓缓推近”。', '在“画幅与尺寸”中选择横屏、竖屏或方形，再在“尺寸”一行选择大小。', '点底栏的秒数，直接修改时长；“可⁠生⁠成⁠范⁠围”中可查看各尺寸的最长时长和预计用时。', '点“生⁠成⁠视⁠频”。描述为空时会先提示，点“继⁠续⁠生⁠成”仍可生成。'],
            en: ['Write the subject, action, place and camera in the prompt, for example "the camera slowly moves closer".', 'Under "Frame & size", choose landscape, portrait or square, then choose a size in the "Size" row.', 'Click the seconds in the bottom bar to change the length. "Available sizes and lengths" shows the longest length and the estimated time for each size.', 'Choose "Generate video". With an empty prompt, a note appears first; choose "Generate anyway" to go ahead.'],
        },
        example: {
            assets: [],
            prompt: ['A white stone castle rises above the mist as the camera slowly moves closer in daylight.', '白石城堡耸立在薄雾之上，日光下镜头缓缓推近。'],
            settings: {quality: 'High', width: 960, height: 544, seconds: 5},
        },
        limits: {
            zh: ['尺寸越大、时长越长，用时和显存占用越多。', '描述中的文字内容不一定能准确显示在画面里。', '开启“提示词增强”后，描述会在本机被改写得更详细。'],
            en: ['A larger size and a longer length take more time and memory.', 'Text named in the prompt may not appear exactly in the picture.', 'With "Prompt enhancement" on, the prompt is rewritten in more detail on this computer.'],
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
        id: 'image-gen',
        title: {zh: '生成图片', en: 'Make a picture'},
        summary: {zh: '在图像视图中用一段描述生成图片，也可以在 AI 全自动中请助手出图。', en: 'Make a picture from a description in the Images view, or ask the assistant for one in Autopilot.'},
        media: {
            type: 'image',
            src: {zh: 'image-gen-1.webp', en: 'image-gen-2.webp'},
            alt: {zh: '图像视图：描述框、比例与清晰度、“生成”和图片下方的“⋯”已用红框标出。', en: 'The Images view, with the prompt, the shape and clarity, "Generate" and the "⋯" below the picture outlined in red.'},
        },
        steps: {
            zh: ['点顶部的“图像视图”。', '在“描述画面”中写下主体、场景和风格；图中要出现的文字，用引号写出原文。', '在“画幅与尺寸”中选择比例，再在“尺寸”中选择“4M”（约 400 万像素）或“16M”（约 1600 万像素）。显存为 8 GB 的显卡，选“4M”。', '点“生成”。图片显示在中间，并加入右侧的作品列表；图片下方一行写明生成它的模型、尺寸和比例。用图片下方一行的图标下载、打开文件夹或复制提示词。图片也会另存一份到 output 文件夹，在“设置”中可以关闭。', '在“AI 全自动”中，连接助手 后说“画一张……”，助手只生成图片，不生成视频；完成后点“打开”查看，点“下载”保存。'],
            en: ['Choose "Images" at the top.', 'In "Describe the picture", write the subject, the place and the style. Put any words that should appear in the picture in quotation marks, exactly as written.', 'Under "Frame & size", choose the shape, then choose "4M" (about 4 megapixels) or "16M" (about 16 megapixels) under "Size". On a graphics card with 8 GB of memory, choose "4M".', 'Choose "Generate". The picture appears in the middle and is added to the works on the right. The line below it names the model that made it, with its size and shape. Use the icons in the line below it to download it, open its folder or copy the prompt. A copy of the picture is also saved to the output folder; this can be switched off in Settings.', 'In Autopilot, connect an assistant and say "Draw a picture of …". The assistant makes only the picture, not a video. When it finishes, choose "Open" to view it and "Download" to save it.'],
        },
        limits: {
            zh: ['未安装图像模型时，“生成”显示为“下载模型”，点它下载；和视频共用的文件不重复下载。', '加参考图见“用参考图生成图片”。选 MOSS 时，点描述框下方的“布局”，在画框上拖出框，每个框写一句放什么。框管位置和大致大小，不会把东西拉伸到填满框。', '“高清”用时更长；显存不足时，出错卡片上可点“改用标准并重试”。'],
            en: ['Without a picture model installed, "Generate" reads "Download model"; choose it to download. Files shared with video are not downloaded again.', 'For reference pictures, see "Make a picture from reference pictures". When MOSS is selected, choose "Layout" below the description, drag boxes on the frame and write what goes in each one. A box sets where a thing goes and roughly how big it is; it does not stretch the thing to fill the box.', '"High" takes longer. If memory runs short, choose "Retry at Standard" on the error card.'],
        },
    },
    {
        id: 'image-references',
        title: {zh: '用参考图生成图片', en: 'Make a picture from reference pictures'},
        summary: {zh: '在图像视图中加入参考图，在描述里用“图 1”“图 2”写明每张图的用途。', en: 'Add reference pictures in the Images view and say what each one is for as "Image 1", "Image 2" in the description.'},
        media: {
            type: 'image',
            src: {zh: 'image-references-1.webp', en: 'image-references-2.webp'},
            alt: {zh: '图像视图：描述框和其下方的参考图（图 1、图 2 和“+”）已用红框标出。', en: 'The Images view, with the description and the reference pictures below it (Image 1, Image 2 and "+") outlined in red.'},
        },
        steps: {
            zh: ['点顶部的“图像视图”，点描述框下方的“参考图”，选“从电脑选择…”或作品中的一张图片；也可以把图片拖到左侧，或粘贴到描述框中。', '参考图按加入的顺序编号为“图 1”“图 2”……在描述中写明每张图的用途，例如“图 1 的人物手里托着图 2 的怀表”。点参考图的缩略图，可以裁剪或修图。', '比例默认为“跟随图片”，也可以改选其他比例；然后点“生成”。', '在结果下方点“编辑”，它会成为图 1，接着描述要改的地方；点“用作参考图”，它会加到参考图的最后。', '把鼠标移到缩略图上，点右上角的“×”，可移除这张参考图。'],
            en: ['Choose "Images" at the top, then "References" below the description, and pick "From this computer…" or a picture from your works. You can also drag pictures onto the left side or paste them into the description.', 'Reference pictures are numbered in the order they were added: "Image 1", "Image 2" and so on. Say in the description what each one is for, for example "the person in Image 1 holds the pocket watch from Image 2". Choose a thumbnail to crop or retouch it.', 'The shape is "Match image" unless you choose another. Then choose "Generate".', 'Below the result, choose "Edit" to make it Image 1 and describe what to change, or "Use as reference" to add it after the other reference pictures.', 'To remove a reference picture, point to its thumbnail and choose "×" at its top right.'],
        },
        limits: {
            zh: ['描述中没有写编号时，所有参考图按顺序一起使用。', 'H3 Image 最多使用 16 张参考图；换用其他图像模型后超出其上限时，会提示需要移除几张。'],
            en: ['When the description names no picture, all reference pictures are used in order.', 'H3 Image uses up to 16 reference pictures. After switching to another picture model, a note says how many to remove if there are more than it takes.'],
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
            zh: ['在“素材”中点“添加”，选择起始图片，并将它的用途下拉框选为“首帧”。', '需要结尾画面时，再添加一张图片并设为“尾⁠帧”。', '在描述中写明两张图之间发生了什么。', '点“生⁠成⁠视⁠频”。'],
            en: ['Under "Media", choose "Add", pick the starting image and set its role dropdown to "First frame".', 'For an ending picture, add another image and set it to "Last frame".', 'Describe what happens between the two images.', 'Choose "Generate video".'],
        },
        example: {
            assets: [{file: 'anime-first.webp', role: 'first'}, {file: 'anime-last.webp', role: 'last'}],
            prompt: ['Anime style, a girl with long dark hair and a school uniform in a sunlit classroom raises her head slowly and turns to look back over her shoulder with a gentle smile, still camera.', '动漫风格，长发校服少女在阳光教室里缓缓抬头，回头看向镜头并微笑，镜头固定。'],
            settings: {},
        },
        limits: {
            zh: ['图片会按所选画幅居中裁切。', 'Mac 上首帧、尾帧与参考图、角色请选其一使用。'],
            en: ['Images are center-cropped to the chosen shape.', 'On a Mac, a request uses either first and last frames, or reference images and characters.'],
        },
    },
    {
        id: 'middle-frames',
        title: {zh: '中间帧', en: 'Middle frames'},
        summary: {zh: '在首帧和尾帧之间再指定最多 3 张画面，并设定各自出现的时间。', en: 'Add up to 3 pictures between the first and last frames, each at a time you choose.'},
        media: {
            type: 'image',
            src: {zh: 'middle-frames-1.webp', en: 'middle-frames-2.webp'},
            alt: {zh: '创作面板：素材中一张图片的用途已选为“中间帧”，用途下拉框和时间输入框已用红框标出。', en: 'The creation panel, with an image in Media set to "Middle frame". The role dropdown and the time field are outlined in red.'},
        },
        steps: {
            zh: ['在“素材”中添加首帧和尾帧两张图片，做法见“首帧和尾帧”一章。', '再点“添⁠加”选一张图片，将它的用途下拉框选为“中间帧”。', '在“时间”中填写这张图出现在第几秒；默认值位于前后两帧的正中，可直接修改。', '需要更多中间画面时重复上两步，最多 3 张，各张时间不能相同。', '在描述中写明画面如何变化，点“生⁠成⁠视⁠频”。'],
            en: ['Under "Media", add a first frame and a last frame, as in the chapter "First and last frames".', 'Choose "Add" again, pick an image and set its role dropdown to "Middle frame".', 'Under "At", enter the second at which this picture appears. The default lies halfway between its neighbors and can be changed.', 'Repeat the last two steps for more pictures, up to 3, each at a different time.', 'Describe how the picture changes, then choose "Generate video".'],
        },
        limits: {
            zh: ['中间帧需要同时有首帧和尾帧，时间需在视频时长以内。', '30 秒及以上的视频分段生成，中间帧只用于第一段。', '接续视频时不使用中间帧，首尾帧会用作参考图。'],
            en: ['Middle frames need both a first and a last frame, and each time must fall within the video.', 'A video of 30 seconds or longer is made in parts, and middle frames apply to the first part only.', 'When continuing a video, middle frames are not used, and the first and last frames are used as references.'],
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
            zh: ['在创作面板的“素材”中添加图片，每张可设为参考、首帧或尾帧。', '在描述中输入 @ 选择图片，并写明它在画面中的作用，例如“<Picture 1> 里的女孩在海边奔跑”。', '视频和音频也可作为参考：同样点“添\u2060加”，在描述中输入 @ 引用；视频卡片上勾选“仅参考构图”时，只沿用它的构图，不沿用动作和颜色。', '音频卡片上选“原样使用”时，成片直接使用这段声音，时长随音频而定；一次只能有一段音频原样使用。', '点“生⁠成⁠视⁠频”。'],
            en: ['Add images under "Media" in the creation panel and set each one as a reference, first frame, or last frame.', 'Type @ in the prompt and choose an image, then say what it does in the scene, for example "The girl in <Picture 1> runs along the beach".', 'Videos and audio can be references too: add them the same way with "Add" and mention them in the prompt with @. On a video card, check "Composition only" to follow its layout but not its motion or colors.', 'On an audio card, choose "Use as is" to put that audio into the result unchanged. The duration follows the audio, and only one audio file can be used as is.', 'Choose "Generate video".'],
        },
        example: {
            assets: [],
            prompt: ['A girl runs along the beach at sunset and waves at the camera.', '一个女孩在日落的海边奔跑，并向镜头挥手。'],
            settings: {},
        },
        limits: {
            zh: ['参考图生成仍在试验中，人物外观可能每次不同；清晰的正面图效果更稳定。', '首帧和尾帧可同时使用，会按画幅居中裁切；也可以与参考图、参考视频同时使用（Mac 上暂不支持）。', '参考视频只取与成片等长的一段，最长 15 秒；生成用时约为只用图片时的两倍。'],
            en: ['Reference generation is experimental, so a character may look different between runs. A clearer, front-facing image helps.', 'First and last frames can be used together and are center-cropped to the frame shape. They can also be used with reference images or videos (not yet on a Mac).', 'A reference video is trimmed to the length of the result, up to 15 seconds, and generation takes about twice as long as with images only.'],
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
            alt: {zh: '角色窗口：名字和正面、侧面、背面三格已用红框标出。', en: 'The character window, with the name and the front, side and back slots outlined in red.'},
        },
        steps: {
            zh: ['展开创作面板的“素材”，在“角色”一行点“+”新建角色。', '填写“名字”，设置图片：可“上⁠传⁠图⁠片”，也可“AI 生成”或“从照片生成”，会生成脸部特写、正面、侧面和背面四张。', '在脸部特写、正面、侧面、背面四格中添加或替换图片，四张齐了再点“保⁠存”。', '之后在描述中输入 @ 选择该角色，并写明其动作。'],
            en: ['Expand "Media" in the creation panel and, on the "Characters" row, choose + to create a character.', 'Enter a "Name" and add pictures: choose "Upload images", or "Generate with AI" or "From a photo", which make a face close-up, front, side and back view.', 'Add or replace images in the face close-up, front, side and back slots; when all four are filled, choose "Save".', 'In later prompts, type @ to pick the character and describe what the character does.'],
        },
        example: {
            assets: [{file: 'fox-sheet.webp', role: 'reference'}],
            prompt: ['Live-action nature film, the fox shown in {image1} runs through a snowy field with soft snowfall, the camera follows beside it.', '纪录片风格，{image1} 中的狐狸在飘雪的田野上奔跑，镜头在一侧跟随。'],
            settings: {},
        },
        limits: {
            zh: ['一次请求最多使用 3 个角色。', 'Mac 上角色与首帧、尾帧请选其一使用。', '角色的外观可能每次略有不同。'],
            en: ['A request uses up to 3 characters.', 'On a Mac, a request uses either characters or first and last frames.', 'A character may look slightly different between runs.'],
        },
    },
    {
        id: 'scenes-props',
        title: {zh: '场景和道具', en: 'Scenes and props'},
        summary: {zh: '把常用的地点和物品存进素材库，在描述中用 @ 引用。', en: 'Save a place or an object once, then mention it with @ in later prompts.'},
        media: {
            type: 'image',
            src: {zh: 'scenes-props-1.webp', en: 'scenes-props-2.webp'},
            alt: {zh: '道具窗口：名称和图片已用红框标出。', en: 'The prop window, with the name and the image outlined in red.'},
        },
        steps: {
            zh: ['展开创作面板的“素材”，在“场景”或“道具”一行点“+”新建。', '填写“名称”，添加图片：可“上传图片”，也可“AI 生成”。场景最多 3 张图片，道具 1 张。', '点“保存”。', '之后在描述中输入 @ 选择它，并写明它在画面中的位置或用法。'],
            en: ['Expand "Media" in the creation panel and choose + on the "Scenes" or "Props" row.', 'Enter a "Name" and add pictures: choose "Upload images" or "Generate with AI". A scene takes up to 3 pictures, a prop 1.', 'Choose "Save".', 'Later, type @ in the description to pick it, and say where it is in the shot or how it is used.'],
        },
        limits: {
            zh: ['一次请求最多使用 1 个场景和 2 个道具，不占角色的 3 个名额。', 'Mac 上场景、道具与首帧、尾帧请选其一使用。', '物品的细节可能每次略有不同。'],
            en: ['A request uses up to 1 scene and 2 props, which do not count toward the 3 characters.', 'On a Mac, a request uses either scenes and props or first and last frames.', 'Fine details of an object may differ slightly between runs.'],
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
            zh: ['替换人物与首帧、尾帧请选其一使用。', '首次使用时会自动下载约 80 MB 的替换人物模型，面板中显示进度，下载完成后才能生成。', '源视频至少 1 秒；源视频没有声音时，不能保留声音。'],
            en: ['A request uses either Replace person or first and last frames.', 'The first time, the Replace person model of about 80 MB downloads on its own. The panel shows the progress, and generation can start when it finishes.', 'The source video must be at least 1 second long. A source video without sound has no sound to keep.'],
        },
    },
    {
        id: 'replace-two-people',
        goto: ['replace-person'],
        title: {zh: '替换两个人物', en: 'Replace two people'},
        summary: {zh: '源视频里有两个人时，用两张人物图分别替换。', en: 'When the source video has two people, replace each with its own picture.'},
        media: {
            type: 'image',
            src: {zh: 'replace-two-people-1.webp', en: 'replace-two-people-2.webp'},
            alt: {zh: '创作面板：“替换人物”开关、两格人物图和“人物图按画面中人物从左到右的顺序对应。”已用红框标出。', en: 'The creation panel, with the "Replace person" switch, the two person image slots and the note on their order outlined in red.'},
        },
        steps: {
            zh: ['展开创作面板的“素材”，打开“替换人物”，在“源视频”中添加视频。', '在“人物图 1”中添加第一张人物图；右边会出现“人物图 2”，再添加第二张。', '人物图按画面中人物从左到右的顺序对应：人物图 1 替换左边的人，人物图 2 替换右边的人。', '点人物图右上角的 × 可以移除一张。', '点“生⁠成⁠视⁠频”。'],
            en: ['Expand "Media" in the creation panel, turn on "Replace person" and add the video under "Source video".', 'Add the first picture under "Person image 1". "Person image 2" appears next to it; add the second picture there.', 'The pictures are matched to the people in the video from left to right: image 1 replaces the person on the left, image 2 the person on the right.', 'Choose × at the top right of a picture to remove it.', 'Choose "Generate video".'],
        },
        limits: {
            zh: ['一次最多替换 2 个人物。', '替换人物与首帧、尾帧请选其一使用。'],
            en: ['Up to 2 people can be replaced in one request.', 'A request uses either Replace person or first and last frames.'],
        },
    },
    {
        id: 'edit-image',
        title: {zh: '编辑图片', en: 'Edit a picture'},
        summary: {zh: '在生成前裁剪、修图或加文字，原图始终保留。', en: 'Crop, retouch or add text before generating. The original is always kept.'},
        media: {
            type: 'image',
            src: {zh: 'edit-image-1.webp', en: 'edit-image-2.webp'},
            alt: {zh: '图片编辑窗口：工具栏中的裁剪、文字和“保存”已用红框标出。', en: 'The picture editor, with the crop and text tools and Save outlined in red.'},
        },
        steps: {
            zh: ['展开创作面板的“素材”，上传图片后点图片，或点图片上方的“编辑”。', '在左侧工具栏选择工具：“裁剪与画布”调整构图，“画笔”“污点修复”“仿制图章”修图，“文字”添加文字。', '改错了可点“撤销”，或按 Ctrl+Z。', '点“保存”。修改后的图片用于生成，原图仍保留；要改回，在图片的下拉菜单中选“改回原图”。'],
            en: ['Expand "Media" in the creation panel, upload a picture, then choose the picture, or "Edit" at its top.', 'Choose a tool on the left: "Crop and canvas" to recompose, "Brush", "Spot healing" or "Clone stamp" to retouch, "Text" to add words.', 'To undo a change, choose "Undo" or press Ctrl+Z.', 'Choose "Save". The edited picture is used for generation and the original is kept. To switch back, choose "Use the original image again" in the picture\'s drop-down.'],
        },
        limits: {
            zh: ['视频的裁剪另在“调整裁剪”中设置。', '“AI 生成”和“点选”需要图片生成功能可用时才显示。'],
            en: ['Crop for a video is set separately, with "Adjust crop".', '"Generate with AI" and "Click select" appear only when picture generation is available.'],
        },
    },
    {
        id: 'local-image-edit',
        title: {zh: '局部改图', en: 'Change part of a picture'},
        summary: {zh: '在编辑图片中框出一块区域，只让 AI 修改这一块，其余部分保持不变。', en: 'Select an area in the image editor and let the AI change only that area, leaving the rest as it is.'},
        media: {
            type: 'image',
            src: {zh: 'local-image-edit-1.webp', en: 'local-image-edit-2.webp'},
            alt: {zh: '编辑图片：图片上框出了一块选区，“选框”工具、描述栏和“生成”按钮已用红框标出。', en: 'The image editor, with an area selected on the picture and the Marquee tool, the description field and the "Generate" button outlined in red.'},
        },
        steps: {
            zh: ['展开创作面板的“素材”，点图片（或图片上方的“编辑”），打开“编辑图片”。', '点左上角的“AI 生成”，选择“改这张”。', '用“选框”“套索”或“魔棒”在图片上选出要修改的区域；按住 Shift 拖动可以添加区域，按住 Alt 拖动可以减去一块。', '在描述栏中写下选区要改成什么样。', '点“生⁠成”。结果出现后选区仍然保留，可以直接再改；满意后点“保存”。'],
            en: ['Expand "Media" in the creation panel, click the picture (or "Edit" at its top) to open "Edit image".', 'Click "Generate with AI" at the top left and choose "Edit this".', 'Select the area to change on the picture with "Marquee", "Lasso" or "Magic wand". Hold Shift while dragging to add an area, or Alt to take one away.', 'In the description field, describe how the selection should change.', 'Click "Generate". The selection stays after the result appears, so it can be changed again; click "Save" when satisfied.'],
        },
        limits: {
            zh: ['没有选区时，“改这张”会按描述修改整张图片。', '“点选”需要先下载一次选区模型，面板会显示进度。', '图片生成需要所用的图片模型已经下载完成。'],
            en: ['Without a selection, "Edit this" changes the whole picture according to the description.', '"Click select" needs a selection model downloaded once; the panel shows the progress.', 'Generating pictures needs the picture model to be downloaded first.'],
        },
    },
    {
        id: 'new-image-shape',
        title: {zh: '在已有图片的画板上生成新图', en: 'A new picture on a canvas that has pictures'},
        summary: {zh: '画板上已有图片时，“生成一张”可以沿用当前画板，也可以换一种比例另起一块画板。', en: 'When the canvas already has pictures, "New image" can use the current canvas or start a canvas of another shape.'},
        media: {
            type: 'image',
            src: {zh: 'new-image-shape-1.webp', en: 'new-image-shape-2.webp'},
            alt: {zh: '编辑图片：“生成一张”和比例列表已用红框标出，比例选为 16:9。', en: 'The image editor, with "New image" and the shape list outlined in red and 16:9 chosen.'},
        },
        steps: {
            zh: ['打开“编辑图片”，点左上角的“AI 生成”，选择“生⁠成⁠一⁠张”。', '旁边的比例列表默认是当前画板的比例；画板不是常用比例时，第一项显示它的尺寸。', '保持当前比例时，新图作为一个新图层加入当前画板。', '选择其他比例时，新图放在该比例的一块新空白画板上。', '写好描述后点“生⁠成”。要回到原来的画板，点一次“撤⁠销”。'],
            en: ['Open "Edit image", click "Generate with AI" at the top left and choose "New image".', 'The shape list beside it starts at the current canvas shape. When the canvas is not one of the usual shapes, its size is listed first.', 'With the current shape kept, the new picture is added to the canvas as a new layer.', 'With another shape chosen, the new picture is placed on a new blank canvas of that shape.', 'Write the description and click "Generate". To return to the previous canvas, click "Undo" once.'],
        },
        limits: {
            zh: ['“改这张”修改当前图片，不显示比例列表。', '画板为空时，比例列表用来设定空白画板的比例。'],
            en: ['"Edit this" changes the current picture and does not show the shape list.', 'On an empty canvas, the shape list sets the shape of the blank canvas.'],
        },
    },
    {
        id: 'lora',
        goto: ['lora-add'],
        title: {zh: '使用自己的 LoRA', en: 'Use your own LoRA'},
        summary: {zh: '把下载好的 LoRA 文件加进创作面板，改变画面风格。', en: 'Add a downloaded LoRA file in the creation panel to change the style of the picture.'},
        media: {
            type: 'image',
            src: {zh: 'lora-1.webp', en: 'lora-2.webp'},
            alt: {zh: '创作面板：“LoRA”中的“添加”按钮、模型下拉列表、强度和左侧把手已用红框标出。', en: 'The creation panel, with the "Add" button under "LoRA", the model list, the strength and the handle on the left outlined in red.'},
        },
        steps: {
            zh: ['把 LoRA 文件（.safetensors）放进 models/loras 文件夹。', '点创作面板的“LoRA”，再点“添加 LoRA”，在下拉列表中选择文件。', '需要时修改右边的强度，默认是 1；取消行首的勾选可暂时停用；有多个 LoRA 时，拖动左侧的把手调整顺序。', '模型要求触发词时，填在“触发词”一栏，它会自动加进描述；移除这个 LoRA 时一并去掉。', '点“生⁠成⁠视⁠频”。'],
            en: ['Put the LoRA file (.safetensors) in the models/loras folder.', 'Choose "LoRA" in the creation panel, then "Add LoRA", and pick the file from the list.', 'Change the strength on the right if needed; the default is 1. Clear the checkbox at the start of the row to switch the LoRA off for now. With more than one LoRA, drag the handle on the left to change the order.', 'If the LoRA asks for trigger words, enter them in "Trigger words". They are added to the prompt, and removed again with the LoRA.', 'Choose "Generate video".'],
        },
        limits: {
            zh: ['一次最多使用 32 个 LoRA。', '只列出 .safetensors 格式的文件。', 'FreeVideo 无需添加加速 LoRA。'],
            en: ['Up to 32 LoRAs can be used at a time.', 'Only .safetensors files are listed.', 'FreeVideo needs no acceleration LoRA.'],
        },
    },
    {
        id: 'quality-tiers',
        title: {zh: '四个质量档位', en: 'The four quality levels'},
        summary: {zh: '档位越靠右，用时越长、细节越多。', en: 'Further right takes longer and keeps more detail.'},
        media: {
            type: 'image',
            src: {zh: 'quality-tiers-1.webp', en: 'quality-tiers-2.webp'},
            alt: {zh: '创作面板：底栏的质量按钮和展开的质量滑块（轻量、标准、精细、极致）已用红框标出。', en: 'The creation panel, with the quality button in the bottom bar and the quality slider it opens (with its four names) outlined in red.'},
        },
        steps: {
            zh: ['点底栏的质量按钮（显示当前档位，如“轻量”），在滑块上选择“轻量”“标准”“精细”或“极⁠致”。', '构思阶段用“轻量”，快速确认画面和动作。', '定稿时改用“精细”或“极⁠致”。', '选好档位后，“生⁠成⁠视⁠频”按钮下方会显示预计用时；然后点它开始生成。'],
            en: ['Choose the quality button in the bottom bar (it shows the current level, such as Light) and move the slider to Light, Medium, High or Max.', 'While exploring, use Light to check the picture and motion quickly.', 'For the final video, switch to High or Max.', 'The estimated time for the chosen level appears under "Generate video". Then choose it to start.'],
        },
        example: {
            assets: [],
            prompt: ['A golden puppy runs across a sunny green meadow toward the camera, ears flopping, soft daylight.', '一只金毛小狗在阳光明媚的绿色草地上朝镜头跑来，耳朵晃动，柔和的日光。'],
            settings: {quality: 'High'},
        },
        limits: {
            zh: ['预计用时因显卡而异，以生成按钮上显示的为准。', '不同档位各自生成，画面构图可能不同，不宜逐帧对比。'],
            en: ['Time depends on the graphics card; use the estimate shown on the Generate button.', 'Each level is generated on its own, so the composition may differ and the results should not be compared frame by frame.'],
        },
    },
    {
        id: 'refine',
        title: {zh: '先出预览，再继续二采', en: 'Preview first, then run the second pass'},
        summary: {zh: '先出低分辨率预览，满意后再生成完整视频。', en: 'Make a lower-resolution preview first, then the full video when it looks right.'},
        media: {
            type: 'image',
            src: {zh: 'refine-1.webp', en: 'refine-2.webp'},
            alt: {zh: '创作面板：“生⁠成⁠视⁠频”右侧的箭头已用红框标出，展开的菜单中有“生⁠成⁠视⁠频”和“生⁠成⁠预⁠览”。', en: 'The creation panel, with the arrow beside "Generate video" outlined in red and its menu open on "Generate video" and "Generate preview".'},
        },
        steps: {
            zh: ['点“生⁠成⁠视⁠频”右侧的箭头，选“生⁠成⁠预⁠览”。', '点“生⁠成⁠预⁠览”，等待预览完成。', '画面满意时，点结果中的“继续二采”；也可以在作品库中右键这条预览，选“继续二采”。', '不满意时，修改描述或种子，再生成新的预览。'],
            en: ['Choose the arrow beside "Generate video" and pick "Generate preview".', 'Choose "Generate preview" and wait for it to finish.', 'When the picture is right, choose "Run second pass" in the result, or right-click the preview in Creations and choose "Run second pass".', 'Otherwise change the prompt or the seed and generate a new preview.'],
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
        id: 'run-modes',
        title: {zh: '一次生成多条：批量、每行一条描述和循环', en: 'Several at once: batch, one prompt per line, loop'},
        summary: {zh: '用同一组设置连续生成多个版本，或一次试几种写法。', en: 'Make several takes with the same settings in a row, or try several prompts at once.'},
        media: {
            type: 'image',
            src: {zh: 'run-modes-1.webp', en: 'run-modes-2.webp'},
            alt: {zh: '“高级选项”窗口：“生成方式”中的“批量”、“生成数量”和“每行一条描述”已用红框标出。', en: 'The Advanced window, with "Batch", "Number of videos" and "One prompt per line" under "Generation mode" outlined in red.'},
        },
        steps: {
            zh: ['点创作面板底部的“高级选项”（滑杆图标）。', '在“生成方式”中选“批量”，填写“生成数量”，再点生成：用当前设置连续生成这么多个视频，每个换一个种子。', '要一次试几种写法，打开“每行一条描述”，在描述框中每行写一条，每行生成一个视频。', '选“循环”后点“开始循环”，会一直生成新版本；点“完成当前后停止”结束。'],
            en: ['Choose "Advanced" (the sliders icon) at the bottom of the creation panel.', 'Under "Generation mode", choose "Batch", set "Number of videos", then generate: that many videos are made in a row with the current settings, each with a new seed.', 'To try several prompts at once, turn on "One prompt per line" and write one prompt per line in the description. Each line becomes one video.', 'Choose "Loop" and then "Start loop" to keep making new takes; choose "Stop after this video" to end it.'],
        },
        limits: {
            zh: ['分段视频每次生成一条，“批量”和“循环”用于单段视频。', '打开“增强”时，每行一条描述按原文发送，不做增强。', '循环在关闭网页后停止。'],
            en: ['Videos made in parts are made one at a time; "Batch" and "Loop" are for single-part videos.', 'With "Enhance" on, each line is sent as written, without enhancement.', 'A loop stops when the page is closed.'],
        },
    },
    {
        id: 'long-video',
        title: {zh: '连续的长视频', en: 'Make a longer video'},
        summary: {zh: '填写更长的时长自动分段生成，也可逐段接续。', en: 'Enter a longer duration to generate in parts, or continue one part at a time.'},
        media: {
            type: 'image',
            src: {zh: 'long-video-1.webp', en: 'long-video-2.webp'},
            alt: {zh: '创作面板：时长输入框和“生⁠成⁠视⁠频”按钮已用红框标出。', en: 'The creation panel, with the duration field and the "Generate video" button outlined in red.'},
        },
        steps: {
            zh: ['填写更长的时长，会自动分段生成；点画幅按钮（如“16:9 · 1M”），在“可⁠生⁠成⁠范⁠围”中可查看各尺寸的最长时长和预计用时。', '点“生⁠成⁠视⁠频”。', '在生成好的视频下方点“接着生成”，继续下一段。', '更长的多镜头视频，可在“剪辑视图”中另起新的镜头，或使用“AI 全自动”。'],
            en: ['Enter a longer duration. It is made in parts automatically. Choose the frame button (such as "16:9 · 1M"); "Available sizes and lengths" there shows how long each size can be and about how long it takes.', 'Choose "Generate video".', 'Choose "Extend" below a finished video to continue it.', 'For a longer video with several shots, start new shots in "Edit", or use "Autopilot".'],
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
        view: 'editor',
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
        id: 'save-frame',
        view: 'editor',
        title: {zh: '保存当前帧', en: 'Save the current frame'},
        summary: {zh: '把时间线上某一刻的画面存成图片，可再用作首帧、参考图或继续修图。', en: 'Keep the picture at one moment of the timeline as an image, to use as a first frame, a reference or for further editing.'},
        media: {
            type: 'image',
            src: {zh: 'save-frame-1.webp', en: 'save-frame-2.webp'},
            alt: {zh: '剪辑视图：时间线上的播放头和预览下方的“保存当前帧”按钮已用红框标出。', en: 'The Edit view, with the playhead on the timeline and the "Save current frame" button below the preview outlined in red.'},
        },
        steps: {
            zh: ['点顶部的“剪辑视图”。', '把播放头拖到想要的画面上，也可按空格播放、暂停后停在该处。', '点预览右下方的“保存当前帧”按钮（相机图标）。', '保存后，在“编辑图片 > AI 生成 > 作品库”中找到这张图片。'],
            en: ['Choose "Edit" at the top.', 'Drag the playhead to the picture you want, or press Space to play and pause there.', 'Choose the "Save current frame" button (camera icon) at the lower right of the preview.', 'Find the saved picture under "Edit image > Generate with AI > Works".'],
        },
        limits: {
            zh: ['保存的图片与导出视频中这一刻的画面相同。', '播放头下方须有片段；正在生成的一段完成后才能保存。', '时间线为空时按钮不可用。'],
            en: ['The saved picture is the same as this moment of the exported video.', 'There must be a clip under the playhead. A part that is still being generated can be saved once it is done.', 'The button is unavailable while the timeline is empty.'],
        },
    },
    {
        id: 'editor-extend-refs',
        view: 'editor',
        title: {zh: '在剪辑视图延长时加参考图', en: 'Add reference images when extending in Edit'},
        summary: {zh: '用参考图引导接着生成的那一段。', en: 'Use reference images to guide the next part.'},
        media: {
            type: 'image',
            src: {zh: 'editor-extend-refs-1.webp', en: 'editor-extend-refs-2.webp'},
            alt: {zh: '剪辑视图：选中的接续段和“素材”按钮已用红框标出。', en: 'The Edit view, with the selected continuation and the Media button outlined in red.'},
        },
        steps: {
            zh: ['选中要接续的一段。', '点“素材”，上传或生成图片，最多 3 张。', '填写描述，然后点“生⁠成”。'],
            en: ['Select the part to continue.', 'Choose "Media" to upload or generate images, up to 3.', 'Describe it, then choose "Generate".'],
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
        view: 'editor',
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
        view: 'editor',
        select: '.fv-ed-clip[data-track=a1]',
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
        view: 'editor',
        goto: ['audio-a2-track'],
        title: {zh: '加配乐', en: 'Add music'},
        summary: {zh: '在声音轨上选一段，描述想要的音乐。', en: 'Select a range on a sound track and describe the music.'},
        media: {
            type: 'image',
            src: {zh: 'replace-music-1.webp', en: 'replace-music-2.webp'},
            alt: {zh: '剪辑视图：第二条声音轨上选中的一段和风格选项已用红框标出。', en: 'The Edit view, with a range on the second sound track and the style options outlined in red.'},
        },
        steps: {
            zh: ['点“＋ 声音轨”，添加第二条声音轨“声音 2”。', '在“声音 2”轨的空白处拖出一段。', '在“风格”和“情绪”中各选一项，或在“描述配乐”中写下想要的音乐。写了描述时，只按描述生成。', '点“生⁠成”。'],
            en: ['Choose "+ Audio track" to add a second audio track, "Audio 2".', 'Drag a range on an empty part of the "Audio 2" track.', 'Choose one option under "Style" and one under "Mood", or describe the music under "Describe the music". When there is a description, the music follows it alone.', 'Choose "Generate".'],
        },
        limits: {
            zh: ['首次生成需要下载配乐模型。', '要换掉分离后的音乐，请选中“音乐”轨上的片段，按钮会变为“替⁠换⁠配⁠乐”。'],
            en: ['The first run downloads the music model.', 'To replace separated music, select a clip on the music track. The button then reads "Replace music".'],
        },
    },
    {
        id: 'beat-snap',
        view: 'editor',
        title: {zh: '对齐音乐节拍', en: 'Cut to the beat'},
        summary: {zh: '“声音 2”轨上有音乐时，拖动片段会吸附到音乐的拍点。', en: 'With music on the "Audio 2" track, clips you drag snap to its beats.'},
        media: {
            type: 'image',
            src: {zh: 'beat-snap-1.webp', en: 'beat-snap-2.webp'},
            alt: {zh: '剪辑视图：正在拖动的画面片段和工具栏的“吸附”按钮已用红框标出，“声音 2”轨上显示灰色拍点线。', en: 'The Edit view, with the clip being dragged and the "Snapping" button outlined in red, and grey beat lines on the "Audio 2" track.'},
        },
        steps: {
            zh: ['点“＋ 声音轨”添加“声音 2”，把音乐放到这条轨上：从“已导入”拖入，或按“加配乐”一章生成。', '确认工具栏的“吸附”按钮（磁铁图标）已打开，也可按 S 键切换。', '拖动画面片段或片段的边缘，“声音 2”轨上会出现灰色的拍点线。', '片段的开头或结尾靠近拍点时会对齐到该拍点，松开鼠标后片段停在这里。'],
            en: ['Choose "+ Audio track" to add "Audio 2", then put music on it: drag it in from "Imported", or make it as in the "Add music" chapter.', 'Make sure the "Snapping" button (magnet icon) in the toolbar is on. The S key turns it on and off.', 'Drag a picture clip or the edge of a clip. Grey beat lines appear on the "Audio 2" track.', 'When the start or end of the clip comes near a beat, it lines up with that beat. Release the mouse to leave it there.'],
        },
        limits: {
            zh: ['只有“声音 2”轨上的音乐有拍点；节奏不明显的音乐没有拍点线。', '拍点线过密时不显示，放大时间线后出现；附近有片段边缘、播放头等其他吸附点时，优先对齐它们。', '拖动时按住 Alt 可临时反转吸附。'],
            en: ['Only music on the "Audio 2" track has beats. Music without a clear pulse shows no beat lines.', 'Beat lines are hidden while they are too close together; zoom in on the timeline to see them. Other snap points nearby, such as clip edges or the playhead, take priority over beats.', 'Hold Alt while dragging to reverse snapping for that drag.'],
        },
    },
    {
        id: 'voice-recording',
        view: 'editor',
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
            zh: ['录音需要允许使用麦克风。', '录音中按 Esc 或点录音条上的“×”，已录的部分保留在轨道上；撤销一次可移除。'],
            en: ['Recording needs permission to use the microphone.', 'Pressing Esc or choosing "×" on the recording bar during a take keeps what has been recorded on the track. One undo removes it.'],
        },
    },
    {
        id: 'narration',
        view: 'editor',
        goto: ['editor-timeline'],
        title: {zh: '为一句话配音', en: 'Add a spoken line'},
        summary: {zh: '写下一句话，用片中的声音或一段录音说出来。', en: 'Write a line and have it spoken in a voice from the project or a recording.'},
        media: {
            type: 'image',
            src: {zh: 'narration-1.webp', en: 'narration-2.webp'},
            alt: {zh: '剪辑视图：右栏的“音色”和生成按钮已用红框标出。', en: 'The Edit view, with "Voice" in the right panel and the generate button outlined in red.'},
        },
        steps: {
            zh: ['在声音轨的空白处点右键，选“配⁠音”。', '配音放在“录⁠音”轨上（分离过音轨时放在“人⁠声”轨），没有录音轨时会自动加上；选区从点击处到下一个片段，最长 5 秒。', '在“音⁠色”中选说话人、导入的片段或录音。“选一个声音片段…”可在时间线上点选，▶ 可试听。', '写下要说的话，点生成按钮。'],
            en: ['Right-click an empty spot on a sound track and choose "Narration".', 'The line goes on the Voice track (or on the Dialogue track after separating tracks), which is added if missing. The range runs from the click to the next clip, up to 5 s.', 'Under "Voice", choose a speaker, an imported clip or a recording. "Pick a sound clip…" lets you click one on the timeline, and ▶ plays a sample.', 'Write what to say and choose the generate button.'],
        },
        limits: {
            zh: ['话比选区长时，后面有空位就按完整长度放入；会盖住后面的片段时不放入，选区和文字保留。', '放入后可以撤销，也可在“台⁠词”中点“重⁠新⁠配⁠音”改写。'],
            en: ['If the line is longer than the selection, it is placed at full length when there is room after it. If it would cover the next clip, it is not placed, and the selection and text are kept.', 'A placed line can be undone, or changed with "Redub" under "Lines".'],
        },
    },
    {
        id: 'sound-to-picture',
        view: 'editor',
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
        id: 'text-subtitles',
        view: 'editor',
        goto: ['text-entry'],
        title: {zh: '加文字和字幕', en: 'Add text and subtitles'},
        summary: {zh: '在剪辑视图里给画面加标题文字，或加上字幕。', en: 'Put a title or subtitles over the picture in the Edit view.'},
        media: {
            type: 'image',
            src: {zh: 'text-subtitles-1.webp', en: 'text-subtitles-2.webp'},
            alt: {zh: '剪辑视图：工具栏中的文字图标和字幕图标已用红框标出。', en: 'The Edit view, with the text icon and the subtitle icon in the toolbar outlined in red.'},
        },
        steps: {
            zh: ['点顶部的“剪辑视图”，把播放头移到想加字的位置。', '点工具栏的 T 图标加标题，或点它右边的字幕图标加一条字幕，然后直接输入内容。', '在时间线上拖动两端调整出现的时间；在右侧面板中改字体、大小和颜色。', '已有 SRT 字幕文件时，选中字幕后点“导入字幕文件”。', '点右上角的“导⁠出”，文字和字幕会一起画进视频。'],
            en: ['Choose "Edit" at the top and move the playhead to where the words should appear.', 'Click the T icon in the toolbar to add a title, or the subtitle icon next to it to add a subtitle, then type.', 'Drag the ends on the timeline to set when they appear, and change the font, size and colour in the panel on the right.', 'With an SRT subtitle file, select a subtitle and choose "Import subtitle file".', 'Choose "Export" at the top right. The words are drawn into the video.'],
        },
        limits: {
            zh: ['字幕文件只支持 SRT，一次最多导入 500 条。', '所选字体缺少的字会在导出前提示。'],
            en: ['Only SRT subtitle files can be imported, up to 500 subtitles at a time.', 'Characters the chosen font does not have are reported before export.'],
        },
    },
    {
        id: 'autopilot',
        title: {zh: 'AI 全自动', en: 'Autopilot'},
        summary: {zh: '用对话说明想拍的内容，由 AI 分镜头并逐个生成。', en: 'Say what to film in a chat; the AI plans the shots and makes them one by one.'},
        media: {
            type: 'image',
            src: {zh: 'autopilot-1.webp', en: 'autopilot-2.webp'},
            alt: {zh: 'AI 全自动：连接助手 的按钮已用红框标出。', en: 'Autopilot, with the button that connects an assistant outlined in red.'},
        },
        steps: {
            zh: ['点顶部的“AI 全自动”。', '点“连接助手”卡片中的按钮复制连接信息，粘贴给所使用的助手。', '状态显示“助手已连接”或所连助手的名字后，在“对助手说…”中描述想做的视频，例如“做一条 1 分钟的竖屏短片：咖啡店的一天”。', '点“发送”，助手会生成并剪辑；可随时点“停止”，完成后点“打开”或“剪辑”。', '在对话列表中点对话旁的“更多”，选“重命名”可修改对话名称。'],
            en: ['Choose "Autopilot" at the top.', 'On the "Connect an assistant" card, choose the button to copy the connection details and paste them to the assistant you use.', 'When it shows "Assistant connected" or the assistant\'s name, describe the video under "Tell the assistant…", for example "Make a 1-minute vertical short film: a day in a coffee shop".', 'Choose "Send". The assistant generates and edits it. Choose "Stop" at any time, and "Open" or "Edit" when it finishes.', 'In the chat list, choose "More" next to a chat, then "Rename" to change its name.'],
        },
        limits: {
            zh: ['需要先连接助手，未连接时只显示连接说明。', '镜头越多，用时越长。', '助手开拍整部片前、重拍耗时较长时是否先询问，可在“设置”的“AI 助手”中选择。'],
            en: ['An assistant must be connected first. Until then only the connection card is shown.', 'More shots take longer.', 'Whether the assistant asks before starting a film or a long reshoot is set under "AI assistant" in Settings.'],
        },
    },
    {
        id: 'drama-outline',
        title: {zh: '短剧的大纲和站位', en: 'The outline and staging of a short drama'},
        summary: {zh: '开拍前，确认卡片上列出助手写好的大纲；需要修改时先不拍，在对话中说明要改什么。', en: 'Before shooting, the confirmation card lists the outline the assistant wrote. To change it, choose not to start yet and say what to change in the chat.'},
        media: {
            type: 'image',
            src: {zh: 'drama-outline-1.webp', en: 'drama-outline-2.webp'},
            alt: {zh: 'AI 全自动：开拍确认卡片上的大纲和“开始”“先不”按钮已用红框标出。', en: 'Autopilot, with the outline on the confirmation card and the "Start" and "Not now" buttons outlined in red.'},
        },
        steps: {
            zh: ['在“AI 全自动”中连接助手，说明想拍的短剧，例如“做一条 1 分钟的竖屏短剧：守塔人收到妹妹的求救信”。', '助手先写一页大纲，每拍一行；再按场写好站位，即每个人在画面中的位置、坐或站、朝向和机位，并用在这一场的每个镜头中。', '开拍前，对话中出现确认卡片：上方是尺寸、时长和预计用时，下方按顺序列出大纲的各拍。超过 8 拍时只显示前 7 拍，点“另外 N 拍”可展开。', '满意时点“开始”；需要修改时点“先不”，再在对话中说明要改哪一拍，助手改好后会再次询问。'],
            en: ['Connect an assistant in Autopilot and describe the short drama, for example "Make a 1-minute vertical short drama: a lighthouse keeper gets a letter from her sister asking for help".', 'The assistant first writes a one-page outline, one line per beat. It then sets the staging of each scene, that is where each person is in the picture, sitting or standing, which way they face and where the camera is, and uses it in every shot of that scene.', 'Before shooting, a confirmation card appears in the chat: the size, length and estimated time at the top, and the beats of the outline in order below. With more than 8 beats only the first 7 are shown; choose "N more" to see the rest.', 'Choose "Start" if it is right. To change it, choose "Not now" and say in the chat which beat to change. The assistant asks again once it is revised.'],
        },
        limits: {
            zh: ['在“设置”的“AI 助手”中取消“助手开拍整部片前先询问”后，不再出现确认卡片，助手直接开拍。', '助手没有写大纲时，卡片上只有尺寸、时长和预计用时。'],
            en: ['With "Ask before the assistant starts a film" turned off under "AI assistant" in Settings, no card appears and the assistant starts at once.', 'When the assistant wrote no outline, the card shows only the size, length and estimated time.'],
        },
    },
    {
        id: 'film-project',
        view: 'editor',
        title: {zh: '短剧成片的剪辑工程', en: 'The edit of a finished short drama'},
        summary: {zh: '短剧拍完后，除了成片视频，还会建好一个按镜头分段的剪辑工程，可以接着修改。', en: 'When a short drama is finished, an edit cut into its shots is made along with the film, ready for further changes.'},
        media: {
            type: 'image',
            src: {zh: 'film-project-1.webp', en: 'film-project-2.webp'},
            alt: {zh: '剪辑视图：项目名和按镜头分段的画面轨已用红框标出。', en: 'The Edit view, with the edit name and the picture track cut into shots outlined in red.'},
        },
        steps: {
            zh: ['AI 全自动拍完一部短剧后，成片保存到作品库，同时建好一个以片名命名的剪辑工程。', '在 AI 全自动的对话中，点成片卡片上的“剪⁠辑”打开这个工程；也可以在顶部的“剪辑视图”中点项目名，在菜单中选这部片的名字。', '画面轨在每个镜头的起点切开；每一段左上角是镜头编号，底部是该镜头的说明。', '可以像其他剪辑一样裁剪、调整顺序、加配乐或字幕，再点右上角的“导⁠出”；作品库中的成片不会改变。'],
            en: ['When Autopilot finishes a short drama, the film is saved to Creations and an edit named after the film is made with it.', 'In the Autopilot conversation, choose "Edit" on the film\'s card to open this edit. Or choose "Edit" at the top, choose the edit name, and pick the film\'s name in the menu.', 'The picture track is cut where each shot starts. Each part shows the shot number at its top left and the shot\'s note along its bottom.', 'Trim, reorder, add music or subtitles as in any other edit, then choose "Export" at the top right. The film in Creations does not change.'],
        },
        limits: {
            zh: ['只有收尾时拼接成功的短剧才有剪辑工程。', '在此版本之前拍完的短剧没有剪辑工程。'],
            en: ['Only a short drama whose shots were joined successfully gets an edit.', 'Short dramas finished before this version have no edit.'],
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
            zh: ['在预览下方的最近作品中点一条以前的视频，或点开头的“作品库”从中选择。', '点“复⁠用⁠设⁠置”，描述、尺寸、时长和种子会填回创作面板。', '改动需要变化的内容，再点“生⁠成⁠视⁠频”。', '填入有误时，10 秒内点“撤⁠销”，恢复原来的内容。'],
            en: ['Pick an earlier video in the recent videos below the preview, or choose "Creations" at its start and pick one there.', 'Choose "Reuse settings". Its prompt, size, duration and seed are filled into the creation panel.', 'Change what should be different, then choose "Generate video".', 'If the fill was a mistake, choose "Undo" within 10 seconds to bring back what was there.'],
        },
        limits: {
            zh: ['没有保存描述的视频不提供“复用设置”。'],
            en: ['A video that kept no prompt does not offer "Reuse settings".'],
        },
    },
    {
        id: 'upscale',
        goto: ['works-entry'],
        title: {zh: '放大视频', en: 'Upscale a video'},
        summary: {zh: '把生成好的视频放大成一个更大的新作品，原视频保留不变。', en: 'Make a larger copy of a finished video as a new creation. The original stays as it is.'},
        media: {
            type: 'image',
            src: {zh: 'upscale-1.webp', en: 'upscale-2.webp'},
            alt: {zh: '作品库中一条视频的右键菜单：两项“放大到 宽 × 高”已用红框标出。', en: 'The right-click menu of a video in Creations, with the two "Upscale to W × H" rows outlined in red.'},
        },
        steps: {
            zh: ['在作品库中右键一条生成好的视频；大图查看和创作页结果下方的“⋯”菜单中也有同样的选项。', '选“放大到 宽 × 高”：尺寸较小的一项放大 1.5 倍，按原来的描述和种子重新生成细节；尺寸较大的一项放大到 4K 级别，逐帧放大。', '放大任务加入队列，完成后作为新作品出现在作品库中。'],
            en: ['In Creations, right-click a finished video. The "⋯" menu in the large view and below a result in the creation panel has the same choices.', 'Choose "Upscale to W × H". The smaller size is 1.5 times larger and redraws detail from the original prompt and seed; the larger size is about 4K, upscaled frame by frame.', 'The upscale is queued and appears in Creations as a new creation when done.'],
        },
        limits: {
            zh: ['生成完成的 24 fps 视频可以放大；预览请先继续二采，再放大。', '本机能生成该尺寸和时长时，才出现放大 1.5 倍的一项；合并或导入的视频可放大到 4K。', '放大后保留视频原有的声音。'],
            en: ['Finished 24 fps videos can be upscaled. For a preview, run the second pass first.', 'The 1.5 times size appears when this computer can generate that size and length. A merged or imported video can be upscaled to 4K.', 'The upscaled video keeps the original sound.'],
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
            alt: {zh: '“作品库”：第一条作品已用红框标出。', en: 'Your creations, with the first video outlined in red.'},
        },
        steps: {
            zh: ['点预览下方最近作品开头的“作品库”。', '选择一条视频，可播放，或点“接着生成”在剪辑视图中继续；要一次下载多个作品，点作品右上角的勾选框选中它，再点其他作品一起选中，然后点“下⁠载”，存成一个压缩包。', '在作品详情中点爱心图标收藏；在左侧选“收⁠藏”只看收藏的作品，选“全⁠部”看所有作品。', '需要成片时，在“剪辑视图”点“导⁠出”。', '在导出窗口选择范围、画质和内容；要更流畅的画面时，在“帧率”中选 48 或 60，中间的画面由显卡补出；要保留无损的声音，在“声音”中选“无损”。再点“导⁠出”。'],
            en: ['Choose "Creations" at the start of the recent videos below the preview.', 'Pick a video to play it, or choose "Continue" to continue it in the Edit view. To download several at once, choose the check box at the top right of a creation, choose the others to add them, then choose "Download" to save them as one zip file.', 'In a creation\'s details, choose the heart to add it to favorites. On the left, choose "Favorites" to see only those, or "All" to see every creation.', 'For a finished file, choose "Export" in the Edit view.', 'In the export window choose the range, quality and content. For smoother motion, choose 48 or 60 under "Frame rate"; the graphics card makes the frames in between. To keep the sound lossless, choose "Lossless" under "Sound". Then choose "Export".'],
        },
        limits: {
            zh: ['删除作品前会确认；被剪辑使用的作品不能删除。', '导出窗口每次打开时，“内容”默认选“画面和声音”，“画质”默认选“高”；窗口里没有“帧率”时，这台电脑不支持 48 和 60 帧导出。', '导出过程中取消不会留下不完整的文件。'],
            en: ['FreeVideo asks before deleting a video, and a video used in an edit cannot be deleted.', 'The export window always opens with "Video and audio" as the content and "High" as the quality; when it has no "Frame rate", this computer cannot export at 48 or 60 fps.', 'Cancelling an export leaves no unfinished file.'],
        },
    },
    {
        id: 'library-organize',
        goto: ['works-entry'],
        title: {zh: '整理作品：标签、筛选和对比', en: 'Organize creations: tags, filters and compare'},
        summary: {zh: '用标签归类作品，按条件筛选，并把两个作品放在一起比较。', en: 'Group creations with tags, narrow the list with filters, and compare two creations.'},
        media: {
            type: 'image',
            src: {zh: 'library-organize-1.webp', en: 'library-organize-2.webp'},
            alt: {zh: '作品库中选中了两个作品：左侧的“标签”和中间对比上方的“并排”“滑块”已用红框标出。', en: 'Creations with two creations selected, with "Tags" on the left and "Side by side" and "Slider" above the comparison outlined in red.'},
        },
        steps: {
            zh: ['在作品库左侧点“新建标签”，输入名称后按 Enter。', '点作品右上角的勾选框选中它，再点其他作品一起选中；在右侧上方点标签图标，选一个标签。之后在左侧点这个标签，只看带有它的作品。', '点上方的“筛⁠选”，按时间、图片来源等条件缩小范围；也可以在搜索框中按提示词或名称查找。', '正好选中两个作品时，画面中间对比显示它们，可切换“并⁠排”或“滑⁠块”。'],
            en: ['On the left of Creations, choose "New tag", type a name and press Enter.', 'Choose the check box at the top right of a creation and choose others to add them. At the top right, choose the tag icon and pick a tag. Later, choose that tag on the left to see only the creations that have it.', 'Choose "Filter" at the top to narrow the list by time, picture source and more, or search by prompt or name in the search box.', 'With exactly two creations selected, they are compared in the middle; switch between "Side by side" and "Slider".'],
        },
        limits: {
            zh: ['标签保存在当前浏览器中，只在这个浏览器里显示。', '删除的作品在 10 秒内可在左侧“最近删除”中恢复。'],
            en: ['Tags are kept in this browser and show only here.', 'A deleted creation can be brought back from "Recently deleted" on the left within 10 seconds.'],
        },
    },
    {
        id: 'system-status',
        title: {zh: '运行状态', en: 'System status'},
        summary: {zh: '在顶栏看显存和显卡温度，在“运行状态”面板里看本机详情，必要时停止生成。', en: 'See the video memory and graphics card temperature in the top bar and this computer in the Run status panel, and stop a generation when needed.'},
        media: {
            type: 'image',
            src: {zh: 'system-status-1.webp', en: 'system-status-2.webp'},
            alt: {zh: '一个视频正在生成时，顶栏的运行状态和展开的“运行状态”面板：顶栏的运行状态和面板底部的“停止”按钮已用红框标出。', en: 'The status in the top bar while a video is being generated, with the Run status panel open; the status and the Stop button at the bottom of the panel are outlined in red.'},
        },
        steps: {
            zh: ['点顶部的“设置”，在“外观”里打开“在运行状态中显示本机数据”。', '没有任务时，顶栏的运行状态显示显存占用和显卡温度；有任务时改为显示任务进度。', '点顶栏的运行状态打开“运行状态”面板：上面是进行中的任务，“本机”下是显存、温度、显卡占用和硬盘剩余，面板开着时每 2 秒更新一次。', '要停下正在生成的视频，点面板底部的“停止”（有任务进行或排队时才有），再在提问下点“停止”确认；正在生成的不会保留，排队的任务会暂停，点“继续队列”恢复。'],
            en: ['Choose "Settings" at the top, and under "Appearance" turn on "Show this computer in the status".', 'With no task running, the status in the top bar shows the video memory in use and the graphics card temperature; while a task runs it shows the task\'s progress instead.', 'Choose the status in the top bar to open the Run status panel: running tasks at the top, and under "This computer" the video memory, temperature, graphics card load and free disk, updated every 2 seconds while the panel is open.', 'To stop the video being generated, choose "Stop" at the bottom of the panel (there while a task runs or waits), then "Stop" again under the question. What is being made is not kept and waiting tasks pause; choose "Resume queue" to go on.'],
        },
        limits: {
            zh: ['显存达到 95% 或温度达到 85 °C 时，数值以红色显示。', '顶栏变窄时先只留显存数字，再只留图标；点图标仍能打开面板。', '不打开这项设置时，顶栏不显示本机数据，也不在后台读取；“运行状态”面板里的“本机”照常显示。'],
            en: ['Video memory at 95% or more, or a temperature of 85 °C or more, is shown in red.', 'As the top bar narrows, it first keeps only the video memory figure, then only the icon; choosing the icon still opens the panel.', 'With this setting off, the top bar shows no figures for this computer and nothing is read in the background; "This computer" in the Run status panel is shown as usual.'],
        },
    },
    {
        id: 'launcher-close',
        title: {zh: '关闭启动器：退出或后台运行', en: 'Closing the launcher: exit or keep running'},
        summary: {zh: '关闭启动器窗口时，可以选择停止 FreeVideo 退出，或让 FreeVideo 在后台继续运行。', en: 'When the launcher window is closed, choose to stop FreeVideo and exit, or keep FreeVideo running in the background.'},
        media: {
            type: 'image',
            src: {zh: 'launcher-close-1.webp', en: 'launcher-close-2.webp'},
            alt: {zh: '启动器的“关闭 FreeVideo？”对话框：“退出”和“后台运行”按钮已用红框标出。', en: 'The launcher\'s "Close FreeVideo?" dialog, with the "Exit" and "Run in background" buttons outlined in red.'},
        },
        steps: {
            zh: ['FreeVideo 运行时关闭启动器窗口，会出现“关闭 FreeVideo？”。', '点“后⁠台⁠运⁠行”（默认按钮，按 Enter 也可以），窗口关闭，FreeVideo 和正在生成的视频继续运行。', '点“退⁠出”，FreeVideo 停止；正在生成的视频也会停止。', '后台运行时，点托盘图标（macOS 为菜单栏图标）或在它的菜单中选“打开 FreeVideo”，重新打开窗口。', '在托盘菜单中选“退⁠出”可以完全退出；只有视频正在生成时会再确认一次。'],
            en: ['Close the launcher window while FreeVideo is running and "Close FreeVideo?" appears.', 'Choose "Run in background" (the default; Enter also works). The window closes, and FreeVideo and any video being generated keep running.', 'Choose "Exit" to stop FreeVideo. A video being generated stops as well.', 'While it runs in the background, click the tray icon (the menu bar icon on macOS) or choose "Open FreeVideo" in its menu to bring the window back.', 'Choose "Exit" in the tray menu to exit completely. It asks again only while a video is being generated.'],
        },
        limits: {
            zh: ['只在 FreeVideo 由启动器启动时询问；由其他方式启动的不会被停止。', '启动器正在安装或下载时关闭窗口，会先询问“暂停并退出？”。', '系统没有托盘时，窗口缩到任务栏。'],
            en: ['It asks only when the launcher started FreeVideo. FreeVideo started another way is not stopped.', 'Closing the window while the launcher is installing or downloading first asks "Pause and close?".', 'Without a system tray, the window goes to the taskbar.'],
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
            zh: ['先阅读错误提示，其中已写明可尝试的操作。', '仍然失败时，点“导出报告”保存诊断信息。', '点“提交问题”，把报告发给我们。', '提示需要重启时，重新启动 FreeVideo。'],
            en: ['Read the error message first. It names what to try.', 'If it still fails, choose "Export report" to save the diagnostic information.', 'Choose "Report a problem" to send us the report.', 'When asked to restart, restart FreeVideo.'],
        },
        limits: {
            zh: ['报告中的路径已做脱敏处理。', '全部下载源都失败时，错误卡片会逐个列出下载源和失败原因。'],
            en: ['Paths in the report are anonymized.', 'When every download source fails, the error card lists each source and the reason it failed.'],
        },
    },
    {
        id: 'offline-reimport',
        title: {zh: '重新导入离线包修复模型文件', en: 'Repair model files by importing the offline packages again'},
        summary: {zh: '模型文件不完整或被改动时，在启动器里把同一份离线包再导入一次，只补齐和替换有问题的文件。', en: 'When model files are incomplete or changed, import the same offline packages again in the launcher; only the files with problems are completed or replaced.'},
        media: {
            type: 'image',
            src: {zh: 'offline-reimport-1.webp', en: 'offline-reimport-2.webp'},
            alt: {zh: '启动器的“确认安装”页：与测试版本不同的文件说明和“确认并继续”按钮已用红框标出。', en: 'The launcher\'s "Ready to install" page, with the note about a file that differs from the tested version and the "Confirm & continue" button outlined in red.'},
        },
        steps: {
            zh: ['在启动器左侧点“安装”，保持原来的安装目录，点“继续”到模型一步。', '选择“离线包安装”，点“选择模型包…”，或把模型 ZIP 拖到“将模型 ZIP 拖到这里，无需解压”处，再点“检查并继续”。', '在“确认安装”页查看“安装内容”：已经装好的模型显示“已安装，无需改动”，与 FreeVideo 测试过的版本不同的文件会列出改名后的名字和大小。', '勾选“我同意安装计划及模型／工具包许可证。”，点“确⁠认⁠并⁠继⁠续”。'],
            en: ['Choose "Installation" on the left of the launcher, keep the same installation folder, and choose "Continue" to reach the models step.', 'Choose "Offline packages", then "Choose model packages…", or drop the model ZIPs on "Drop model ZIPs here — no extraction needed", and choose "Check & continue".', 'Look over "Installation details" on the "Ready to install" page. Models already in place show "Installed; no change needed", and a file that differs from the version FreeVideo tests is listed with the name it will be kept under and its size.', 'Tick "I accept the installation plan and model / toolkit licenses." and choose "Confirm & continue".'],
        },
        limits: {
            zh: ['已装好的模型直接沿用，不重复占用硬盘空间；下载中断留下的不完整文件会自动补齐。', '内容不同的文件改名保留在原文件夹，确认不再需要后可以自行删除。', '网页里提示模型文件不完整时，同样按本章重新导入。'],
            en: ['Models already in place are reused and take no extra disk space. Files cut short by an interrupted download are completed.', 'A file with different contents is kept under a new name in its folder; delete it once it is no longer needed.', 'When the page reports incomplete model files, import again as this chapter describes.'],
        },
    },
];

// Other names people use for a chapter (not words from its text): the command palette and the tutorial's own search
// find the chapter by these as well as by its title.
export const TUTORIAL_ALIASES = {
    'quick-start': {zh: ['入门', '新手', '第一次'], en: ['getting started', 'beginner', 'first time']},
    'text-to-video': {zh: ['文生视频', '文字转视频'], en: ['text to video', 't2v']},
    'timed-lines': {zh: ['台词', '分镜', '时间轴'], en: ['dialogue', 'storyboard', 'timestamps']},
    'image-gen': {zh: ['文生图', '画图', '出图'], en: ['text to image', 'image generation']},
    'image-references': {zh: ['图生图', '参考图出图'], en: ['image to image', 'img2img']},
    'first-last-frame': {zh: ['首尾帧', '起始帧', '结束帧', '首帧', '尾帧'], en: ['start frame', 'end frame', 'first frame', 'last frame']},
    'middle-frames': {zh: ['关键帧', '插帧'], en: ['keyframe', 'keyframes']},
    'reference-video': {zh: ['图生视频', '参考生视频'], en: ['image to video', 'i2v']},
    'cast-sheet': {zh: ['人物', '角色一致', '三视图', '人设'], en: ['character', 'consistent character', 'turnaround']},
    'scenes-props': {zh: ['背景', '场景图', '道具图'], en: ['background', 'location', 'prop']},
    'replace-person': {zh: ['换人', '换脸', '换角色'], en: ['swap person', 'face swap', 'swap character']},
    'replace-two-people': {zh: ['双人替换', '换两个人'], en: ['two-person swap', 'swap two people']},
    'edit-image': {zh: ['改图', '修图'], en: ['image edit', 'retouch']},
    'local-image-edit': {zh: ['局部重绘', '涂抹', '蒙版'], en: ['inpaint', 'inpainting', 'mask']},
    'new-image-shape': {zh: ['画板', '扩图', '画布'], en: ['canvas', 'outpaint', 'board']},
    'lora': {zh: ['风格模型', '微调模型'], en: ['style model', 'fine-tuned model']},
    'quality-tiers': {zh: ['画质', '档位', '清晰度', '速度'], en: ['quality', 'speed', 'resolution']},
    'refine': {zh: ['预览', '二次采样', '精修'], en: ['preview', 'second pass', 'refine']},
    'run-modes': {zh: ['批量生成', '多个版本', '循环生成', '多条描述'], en: ['batch', 'many takes', 'loop', 'multiple prompts']},
    'long-video': {zh: ['长视频', '多段', '连续生成'], en: ['long video', 'longer', 'multi-part']},
    'extend-video': {zh: ['续写', '延长', '接着生成', '视频续写'], en: ['extend', 'continue video', 'make longer']},
    'continue-or-new-shot': {zh: ['换镜头', '新镜头', '转场'], en: ['new shot', 'cut', 'transition']},
    'editor': {zh: ['剪辑', '剪视频', '裁剪', '时间线'], en: ['edit', 'trim', 'timeline', 'crop']},
    'save-frame': {zh: ['截图', '导出图片', '截帧'], en: ['screenshot', 'still', 'export frame']},
    'editor-extend-refs': {zh: ['延长参考图', '续写参考图'], en: ['extend with reference', 'reference when extending']},
    'segment-prompt': {zh: ['分段描述', '分段提示词'], en: ['per-part prompt', 'segment prompt']},
    'separate-tracks': {zh: ['人声分离', '去背景音', '分离人声', '换声音'], en: ['vocal separation', 'remove music', 'stems']},
    'replace-music': {zh: ['配乐', '背景音乐', 'BGM', '音乐'], en: ['music', 'soundtrack', 'background music', 'bgm']},
    'beat-snap': {zh: ['卡点', '踩点', '节拍'], en: ['beat sync', 'beat', 'rhythm']},
    'voice-recording': {zh: ['配音', '录音', '旁白', '解说'], en: ['voice-over', 'voiceover', 'dub', 'narrate', 'record']},
    'narration': {zh: ['配音', '朗读', '文字转语音', '说话'], en: ['text to speech', 'tts', 'dub', 'speech']},
    'sound-to-picture': {zh: ['对口型', '口型', '音频生视频', '数字人'], en: ['lip sync', 'lipsync', 'audio to video', 'talking head']},
    'text-subtitles': {zh: ['字幕', '标题', '文字'], en: ['subtitles', 'captions', 'srt', 'title']},
    'autopilot': {zh: ['自动', '一键成片', '助手', '智能体'], en: ['auto', 'one click', 'assistant', 'agent']},
    'drama-outline': {zh: ['短剧', '剧本', '大纲'], en: ['short drama', 'script', 'outline']},
    'film-project': {zh: ['成片', '短剧剪辑'], en: ['finished film', 'drama edit']},
    'library-reuse': {zh: ['同款', '复用', '再生成一次'], en: ['reuse', 'same settings', 'remix']},
    'upscale': {zh: ['超分', '4K', '提高分辨率', '变大'], en: ['4K', 'enlarge', 'higher resolution', 'super resolution']},
    'library-organize': {zh: ['分类', '归类', '整理', '找作品', '回收站'], en: ['organize', 'sort', 'find a video', 'trash', 'side by side']},
    'library-export': {zh: ['导出', '下载', '保存视频', '收藏'], en: ['export', 'download', 'save video', 'favorites']},
    'system-status': {zh: ['显存', '内存', '状态', '占用'], en: ['vram', 'memory', 'gpu', 'status']},
    'launcher-close': {zh: ['后台', '退出', '关闭'], en: ['background', 'quit', 'exit']},
    'troubleshoot': {zh: ['报错', '出错', '失败', '问题', '诊断报告'], en: ['error', 'failed', 'problem', 'diagnostic report']},
    'offline-reimport': {zh: ['离线包', '修复模型', '模型损坏', '重新导入'], en: ['offline package', 'repair models', 'broken model', 'reimport']},
};
