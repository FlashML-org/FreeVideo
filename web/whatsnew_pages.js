// The what's-new pages as data, after the storyboard: rows, clips, the generation bar and the lines panel, and timed steps.
// Every text is [English, Chinese] and the editor's own wording. A clip is {id, row, at, to} in fractions of the ruler; a step is
// [ms, ops] and the ops are applied by the scene in whatsnew.js.
const V = {id: 'v1', kind: 'video', name: ['Video', '画面'], height: 40};
const SOUND = [{id: 'a1', kind: 'audio', name: ['Audio', '声音'], height: 40, dialogue: ['Dialogue', '人声']},
    {id: 'a2', kind: 'audio', name: ['Music', '音乐'], height: 40, late: true}, {id: 'a3', kind: 'audio', name: ['Ambience', '环境声'], height: 40, late: true}];
const picture = {id: 'pic', row: 'v1', at: 0, to: 1, picture: true};
const BLOCKS = [[.04, .26], [.34, .58], [.66, .84]];
const blocks = (extra = {}) => BLOCKS.map(([at, to], i) => ({id: `v${i + 1}`, row: 'a1', at, to, wave: [5 + i * 3, 'speech'], off: true, splits: true, ...extra}));
const music = {id: 'music', row: 'a2', at: 0, to: 1, wave: [7, 'piano'], off: true, splits: true};
const guitar = {id: 'guitar', row: 'a2', at: 0, to: 1, wave: [13, 'guitar', 1, BLOCKS], off: true};
const rain = {id: 'rain', row: 'a3', at: 0, to: 1, wave: [9, 'ambience'], off: true, splits: true};
const mix = {id: 'mix', row: 'a1', at: 0, to: 1, wave: [3, 'mixed']};
const redub = {id: 'v2n', row: 'a1', at: BLOCKS[1][0], to: BLOCKS[1][1], wave: [21, 'speech', 1.7], off: true};
export const LINES = [
    [['I have been waiting here for you to come back.', '我一直在这里等你回来。']],
    [['Let us talk about this tomorrow.', '这件事我们明天再说。'], ['tomorrow', '明天'], ['now', '现在']],
    [['The rain seems to be letting up.', '雨好像小了。']],
];

export const PAGES = [
    {id: 'overview', title: ['0.5.0: A fully upgraded sound system', '0.5.0 声音系统全面升级'], length: 11000, height: 400, list: [
        ['Separate tracks: dialogue, music, ambience', '分离音轨：人声、音乐、环境声'], ['Replace music from one line of description', '替换配乐：写一句描述就换'],
        ['Generate music to fit the selected length', '加配乐：按选中的长度生成'], ["Edit lines; only changed lines are redubbed", '改台词：只重配改过的那句'],
        ["Redub in the original speaker's voice", '配音：沿用原来说话人的声音'], ['Record straight onto an audio track', '录音：直接录进声音轨'],
        ['Generate picture from sound', '按声音生成画面']],
    rows: [V, ...SOUND], toolbar: ['stems'], clips: [picture, mix, ...blocks(), music, guitar, rain, redub], preview: false,
    steps: [[0, {}], [1500, {select: 'mix', tb: {show: 'stems'}}], [1900, {tb: {press: 'stems'}, bar: {mode: 'running', hint: [['Separating the sound', '正在分离声音']]}, run: {id: 'mix', ms: 1200}}],
        [3200, {tb: {press: null, hide: true}, split: true, select: null, lit: [0], bar: {mode: 'lines'}, lines: {shown: true, active: -1}}],
        [4600, {select: 'music', lit: [1], bar: {mode: 'selected', presets: true, style: 'piano', mood: 'calm', ph: ['Describe the music', '描述配乐'], type: ['upbeat acoustic guitar', '轻快的原声吉他'], button: ['Replace music', '替换配乐']}, lines: {shown: false}}],
        [5400, {bar: {button: ['Replace music · about 30 s', '替换配乐 · 约 30 秒']}}],
        [5800, {bar: {mode: 'running', hint: [['Generating music', '正在生成配乐'], ['about 30 s', '约 30 秒']]}, run: {id: 'music', ms: 1200}}],
        [7000, {done: 'music', hide: ['music'], show: ['guitar'], reveal: {id: 'guitar', ms: 500}, duck: true}],
        [7400, {select: 'v2', lit: [3, 4], duck: false, bar: {mode: 'lines', presets: false, button: ['Redub', '重新配音']}, lines: {shown: true, active: 1, edit: 'strike'}}],
        [7700, {bar: {button: ['Redub 1 line', '重新配音 1 句']}}], [7800, {lines: {edit: 'typing'}}],
        [8400, {bar: {mode: 'running', hint: [['Dubbing', '正在配音'], ['about 6 s', '约 6 秒']]}, run: {id: 'v2', ms: 1000}}],
        [9400, {done: 'v2', hide: ['v2'], show: ['v2n'], flash: 'v2n', bar: {mode: 'lines', button: ['Redub', '重新配音']}, lines: {edit: 'done'}, play: [0, 1, 1400]}]]},
];
