// Pending cards become available by adding their bundled files and changing this data.
const copy = (en, zh) => ({en, zh});
const clip = name => ({mp4: `${name}.mp4`, poster: `${name}.webp`});
export const CASTLE_PROMPT = ['A white stone castle rises above the mist as the camera slowly moves closer in daylight.', '白石城堡耸立在薄雾之上，日光下镜头缓缓推近。'];
const card = (id, title, line, input, preview, tryit, extra = {}) => ({id, added: '0.4.21', title, line, input, clip: preview, tryit, ...extra});
export const FEATURE_TOUR = [
    card('text-to-video', copy('Text to video', '文字生成视频'), copy('Write a description and get a video.', '写一段描述，生成一段视频。'),
        [{text: copy(...CASTLE_PROMPT)}], clip('c1-s2'), {assets: [], prompt: CASTLE_PROMPT, settings: {quality: 'High', width: 1344, height: 768, seconds: 5}}),
    card('product-showcase', copy('Product showcase', '产品展示'), copy('Turn a whitebox and a product image into a product video.', '用白模和一张产品图，生成产品视频。'),
        [{src: 'c2-whitemodel.webp'}, {src: 'c2-product.webp'}], clip('c2g-s3'), {example: 'whitebox-product', status: 'pending-assets'}, {trigger: 'media'}),
    card('first-last-frame', copy('First and last frame', '首帧和尾帧'), copy('Set the opening and closing frames, and the motion in between is generated.', '给出开头和结尾，生成中间的画面。'),
        [{text: copy('First frame + last frame', '首帧 + 尾帧')}], clip('c3-s1'), {status: 'pending-assets'}, {trigger: 'media'}),
    card('quality-tiers', copy('Four quality levels', '四档质量'), copy('Higher levels add detail and take longer.', '档位越高，画面越细，用时越长。'),
        [{text: copy('A puppy runs across the grass.', '一只小狗跑过草地。')}], clip('c4-high'), {assets: [], settings: {quality: 'High'}},
        {quality: ['light', 'medium', 'high', 'max'].map((name, i) => ({...clip(`c4-${name}`), label: copy(['Light', 'Medium', 'High', 'Max'][i], ['轻量', '标准', '精细', '极致'][i])}))}),
    card('reference-image', copy('Reference image', '参考图'), copy('The same character stays consistent across scenes.', '换了场景，角色仍是同一个。'),
        [{src: 'fox-sheet.webp'}, {text: copy('A fox walks through a forest.', '狐狸走过森林。')}, {text: copy('A fox walks through snow.', '狐狸走过雪地。')}], clip('c5-forest-s1'),
        {assets: [{file: 'fox-sheet.webp', role: 'reference'}], prompt: ['The fox in {image1} walks through a sunlit forest.', '{image1} 中的狐狸走过洒满阳光的森林。'], settings: {}}, {sequence: [clip('c5-forest-s1'), clip('c5-snow-s1')]}),
    card('replace-person', copy('Replace person', '替换人物'), copy('Put the person from an image into a video.', '把视频里的人换成图中的人物。'),
        [{src: 'c10-source.webp'}, {src: 'c10-person.webp'}], clip('c10-s1'), {status: 'pending-assets'}, {trigger: 'media', added: '0.4.28'}),
    card('first-video', copy('Make your first video', '做出第一条视频'), copy('Write a description, generate, and find the video in Creations.', '写一段描述，生成，再到作品中找到它。'),
        [{text: copy(...CASTLE_PROMPT)}], clip('c11-s1'), {assets: [], prompt: CASTLE_PROMPT, settings: {}}, {added: '0.5.0', goto: ['prompt-box', 'generate-btn', 'works-entry']}),
    card('character', copy('Characters', '角色'), copy('Save a character once, then mention it with @ in any description.', '角色保存一次，之后在任意描述中用 @ 引用。'),
        [{text: copy('@Leo walks along the shore.', '@小林 走在海边。')}], clip('c12-s1'), {status: 'pending-assets'}, {trigger: 'media', added: '0.5.0', goto: ['cast-add', 'prompt-box']}),
    card('edit-view', copy('Edit view', '剪辑视图'), copy('Arrange clips on the timeline and adjust picture, sound and speed.', '在时间线上排列片段，调整画面、声音和速度。'),
        [{src: 'c12-s1.webp'}], clip('c13-s1'), {status: 'pending-assets', view: 'editor'}, {trigger: 'editor', added: '0.5.0', goto: ['editor-timeline', 'editor-inspector']}),
    card('separate-sound', copy('Separate and replace sound', '分离与替换声音'), copy('Split the sound into dialogue, music and ambience, then replace each one.', '把声音分成人声、音乐和环境声，再分别替换。'),
        [{src: 'cb-source.webp'}], clip('cb-source'), {status: 'pending-assets', view: 'editor'},
        {trigger: 'editor', added: '0.5.0', goto: ['audio-track-button'], sequence: [['source', 'Original', '原声'], ['dialogue', 'Dialogue', '人声'], ['music', 'Music', '音乐'], ['ambience', 'Ambience', '环境声']].map(([name, en, zh]) => ({...clip(`cb-${name}`), label: copy(en, zh)}))}),
    card('extend', copy('Extend', '接着生成'), copy('Continue from where the last clip ends.', '从上一段的结尾接着生成下一段。'),
        [{src: 'c6-s1.webp'}], clip('c6-s1'), {status: 'pending-assets', view: 'editor'}, {trigger: 'editor', previewRange: [2, 8]}),
    card('two-pass', copy('Two-pass', '二次采样'), copy('The first pass sets the composition; the second adds detail.', '第一遍定构图，第二遍补细节。'),
        [{...clip('c7-src-s1')}], clip('c7-s1'), {assets: [], prompt: ['A dark castle emerges from the mist as the camera slowly moves closer.', '深色城堡从薄雾中显现，镜头缓缓推近。'], settings: {two_pass: true}}),
    card('edit-image', copy('Edit image', '编辑图片'), copy('Change only the selected area and keep the rest.', '只改选中的区域，其余保持不变。'),
        [{src: 'c8-original.webp'}, {text: copy('Make the sneaker red.', '把球鞋改成红色。')}], clip('c8-s1'), {status: 'pending-assets'}),
    card('autopilot', copy('Autopilot', 'AI 全自动'), copy('Describe the video, and AI writes the shots, generates, and edits them.', '说出想要的视频，AI 写分镜、生成并剪辑。'),
        [{text: copy('Make a 15-second sneaker ad.', '做一条 15 秒的球鞋广告。')}], clip('c9-s1'), {status: 'pending-assets'}, {trigger: 'autopilot'}),
];
export const tourKey = card => `tour:${card.id}:${card.added}`;
export const readyCards = (cards = FEATURE_TOUR) => cards.filter(c => c.status !== 'pending' && c.clip?.mp4 && c.clip?.poster);
export const unseenCards = (record, {version, trigger, cards = FEATURE_TOUR} = {}) => readyCards(cards).filter(c =>
    !record?.seen?.includes(tourKey(c)) && (trigger ? c.trigger === trigger : c.added === version));
