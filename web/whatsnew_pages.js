// The what's-new pages as data, after the storyboard: rows, clips, the generation bar and the lines panel, and timed steps.
// Every text is [English, Chinese] and the editor's own wording. A clip is {id, row, at, to} in fractions of the ruler; a step is
// [ms, ops] and the ops are applied by the scene in whatsnew.js.
const V = {id: 'v1', kind: 'video', name: ['Video', '画面'], height: 40};
const SOUND = [{id: 'a1', kind: 'audio', name: ['Audio', '声音'], height: 40, dialogue: ['Dialogue', '人声']},
    {id: 'a2', kind: 'audio', name: ['Music', '音乐'], height: 40, late: true}, {id: 'a3', kind: 'audio', name: ['Ambience', '环境声'], height: 40, late: true},
    {id: 'a4', kind: 'audio', name: ['Sound', '声音'], height: 40, late: true, record: true}];
const picture = {id: 'pic', row: 'v1', at: 0, to: 1, picture: true};
const BLOCKS = [[.04, .26], [.34, .58], [.66, .84]];
const blocks = (extra = {}) => BLOCKS.map(([at, to], i) => ({id: `v${i + 1}`, row: 'a1', at, to, wave: `v${i + 1}`, off: true, splits: true, ...extra}));
const music = {id: 'music', row: 'a2', at: 0, to: 1, wave: 'music', off: true, splits: true};
const guitar = {id: 'guitar', row: 'a2', at: 0, to: 1, wave: 'guitar', off: true};
const rain = {id: 'rain', row: 'a3', at: 0, to: 1, wave: 'rain', off: true, splits: true};
const mix = {id: 'mix', row: 'a1', at: 0, to: 1, wave: 'mix'};
const take = {id: 'take', row: 'a4', at: .3, to: .52, wave: 'take', off: true, grow: true};
const redub = {id: 'v2n', row: 'a1', at: BLOCKS[1][0], to: BLOCKS[1][1], wave: 'v2n', off: true};
export const LINES = [
    [['I have been waiting here for you to come back.', '我一直在这里等你回来。']],
    [['Let us talk about this tomorrow.', '这件事我们明天再说。'], ['tomorrow', '明天'], ['now', '现在']],
    [['The rain seems to be letting up.', '雨好像小了。']],
];

// A page is shown from the version in its `since` on, and `seen` is the record it leaves once seen: [key, value] in local
// storage. The two 0.5 pages keep the records they had in 0.5, so nobody who saw them sees them again.
export const PAGES = [
    // The 0.6.0 page: the creation panel, a work playing on the stage and earlier works open from Media; one picture per language.
    {id: 'interface', kind: 'still', since: '0.6.0', seen: ['freevideo.whatsnew-seen-interface', '1'], title: ['0.6.0: A new interface', '0.6.0 全新界面'],
        poster: {src: ['assets/whatsnew/interface-en.webp', 'assets/whatsnew/interface-zh.webp']},
        list: [['See and manage every running and queued video and picture from any page', '在任何页面都能看到和管理所有生成中、排队的视频和图片'],
            ['Switching pages keeps generation running, along with your prompt and references', '切换页面不会中断生成，描述和参考图都还在'],
            ['Use earlier videos directly as references', '以前生成的视频也能直接用作参考'],
            ['Hand-drawn sketches give finer control over image generation', '可以通过手绘草图更精细地控制生成']]},
    {id: 'overview', since: '0.5.0', seen: ['freevideo.whatsnew-seen', '0.5'], title: ['0.5.0: A fully upgraded sound system', '0.5.0 声音系统全面升级'], length: 11000, height: 400, list: [
        ['Separate tracks: dialogue, music, ambience', '分离音轨：人声、音乐、环境声'], ['Replace music: from a text description', '替换配乐：根据文字描述重新生成'],
        ['Add music: generated to fit the selected length', '添加配乐：按选中的长度生成'], ['Edit lines: only changed lines are redubbed', '修改台词：只为改过的台词重新配音'],
        ["Dub: in the original speaker's voice", '配音：沿用原说话人的声音'], ['Record: onto an audio track', '录音：录制到声音轨'],
        ['Generate picture from sound', '按声音生成画面']],
    rows: [V, ...SOUND], menuItems: [['Copy', '复制'], ['Delete', '删除'], '-', ['Separate tracks', '分离音轨']],
    clips: [picture, mix, ...blocks(), music, guitar, rain, redub, take], preview: false,
    steps: [[0, {at: .12, bar: {mode: 'idle', ph: ['Describe this part', '描述这一段'], button: ['Generate', '生成']}}],
        [1200, {select: 'mix', lit: [0], menu: {show: true, hover: -1}}], [1900, {menu: {show: true, hover: 2}}],
        [2600, {menu: {show: false}, bar: {mode: 'running', hint: [['Separating the sound', '正在分离声音']]}, run: {id: 'mix', ms: 1200}}],
        [3900, {split: true, select: null, bar: {mode: 'idle'}}],
        [5000, {select: 'music', lit: [1], bar: {mode: 'selected', presets: true, style: 'piano', mood: 'calm', ph: ['Describe the music', '描述配乐'], type: ['upbeat acoustic guitar', '轻快的原声吉他'], button: ['Replace music', '替换配乐']}}],
        [5800, {bar: {button: ['Replace music · about 30 s', '替换配乐 · 约 30 秒']}}],
        [6200, {bar: {mode: 'running', hint: [['Generating music', '正在生成配乐'], ['about 30 s', '约 30 秒']]}, run: {id: 'music', ms: 1800}}],
        [8200, {done: 'music', hide: ['music'], show: ['guitar'], reveal: {id: 'guitar', ms: 500}, select: null, bar: {mode: 'idle', presets: false, text: null, button: ['Generate', '生成'], ph: ['Describe this part', '描述这一段']}}],
        [8800, {select: 'v2', lit: [3, 4], bar: {mode: 'lines', button: ['Redub', '重新配音']}, lines: {shown: true, active: 1, edit: 'strike'}}],
        [9100, {bar: {button: ['Redub 1 line', '重新配音 1 句']}}], [9200, {lines: {edit: 'typing'}}],
        [9900, {bar: {mode: 'running', hint: [['Dubbing', '正在配音'], ['about 6 s', '约 6 秒']]}, run: {id: 'v2', ms: 1400}}],
        [11300, {done: 'v2', hide: ['v2'], show: ['v2n'], flash: 'v2n', select: null, bar: {mode: 'lines', button: ['Redub', '重新配音']}, lines: {edit: 'done'}}],
        [12600, {lit: [5], select: 'take', rowShow: 'a4', lines: {shown: false}, bar: {mode: 'record', phase: 'idle'}}],
        [13400, {bar: {phase: 'recording'}, rec: {id: 'take', from: .3, to: .52, ms: 3000}, play: [.3, .52, 3000]}],
        [16500, {bar: {mode: 'idle', phase: null, text: null, button: ['Generate', '生成'], ph: ['Describe this part', '描述这一段']}, select: null, lit: []}]]},
    // The picture page comes from 0.5.1: one real H3 Image output (the tea poster), with its prompt typed over it.
    {id: 'image', kind: 'still', since: '0.5.1', seen: ['freevideo.whatsnew-seen-image', '1'], open: 'image', title: ['H3 Image', 'H3 Image'],
        poster: {src: 'assets/image/h3-7.webp', prompt: ['A quiet Chinese poster: tea hills in morning mist, with the brushed title “Tea in the Hills” down the right side',
            '一张清雅的中式海报：晨雾中的层层茶山，右侧竖排毛笔大字“山间茶事”']},
        list: [['Images view: a picture from one sentence', '图像视图：一句话生成图片'], ['Text in pictures, such as poster titles', '图中可写文字，如海报标题'],
            ['The image model can be installed on its own', '可只安装图像模型']]},
];

// Versions compare as major.minor.patch; anything after the patch number (a build or a pre-release tag) is ignored.
const parts = version => {
    const match = /^v?(\d+)\.(\d+)(?:\.(\d+))?/.exec(String(version ?? '').trim());
    return match ? [Number(match[1]), Number(match[2]), Number(match[3] || 0)] : null;
};
export const atLeast = (version, since) => {
    const [a, b] = [parts(version), parts(since)];
    if (!a || !b) return false;
    for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] > b[i];
    return true;
};
// Every record starts with this, so a page that holds only these has not been used before.
export const SEEN_PREFIX = 'freevideo.whatsnew-seen';
export const seenRecord = page => page.seen || [`${SEEN_PREFIX}-${page.id}`, '1'];
// The pages a version has, in their order; an unknown version has none.
export const pagesFor = (version, pages = PAGES) => pages.filter(page => atLeast(version, page.since));
// Those of them not yet seen; read(key) returns the stored value or null.
export const unseenPages = (version, read, pages = PAGES) => pagesFor(version, pages).filter(page => { const [key, value] = seenRecord(page); return read(key) !== value; });
export const hasWhatsNew = version => pagesFor(version).length > 0;
