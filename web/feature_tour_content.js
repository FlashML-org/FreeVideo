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
