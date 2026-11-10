// The what's-new pages as data, after the storyboard: rows, clips, the generation bar and the lines panel, and timed steps.
// Every text is [English, Chinese] and the editor's own wording. A clip is {id, row, at, to} in fractions of the ruler; a step is
// [ms, ops] and the ops are applied by the scene in whatsnew.js.
const V = {id: 'v1', kind: 'video', name: ['Video', '画面'], height: 40};
const SOUND = [{id: 'a1', kind: 'audio', name: ['Audio', '声音'], height: 40, dialogue: ['Dialogue', '人声']},
    {id: 'a2', kind: 'audio', name: ['Music', '音乐'], height: 40, late: true}, {id: 'a3', kind: 'audio', name: ['Ambience', '环境声'], height: 40, late: true},
    {id: 'a4', kind: 'audio', name: ['Sound', '声音'], height: 40, late: true, record: true}];
const picture = {id: 'pic', row: 'v1', at: 0, to: 1, picture: true};
const BLOCKS = [[.04, .26], [.34, .58], [.66, .84]];
const blocks = (extra = {}) => BLOCKS.map(([at, to], i) => ({id: `v${i + 1}`, row: 'a1', at, to, wave: [5 + i * 3, 'speech'], off: true, splits: true, ...extra}));
const music = {id: 'music', row: 'a2', at: 0, to: 1, wave: [7, 'piano'], off: true, splits: true};
const guitar = {id: 'guitar', row: 'a2', at: 0, to: 1, wave: [13, 'guitar', 1, BLOCKS], off: true};
const rain = {id: 'rain', row: 'a3', at: 0, to: 1, wave: [9, 'ambience'], off: true, splits: true};
const mix = {id: 'mix', row: 'a1', at: 0, to: 1, wave: [3, 'mixed']};
const take = {id: 'take', row: 'a4', at: .3, to: .52, wave: [17, 'speech', 1.2], off: true, grow: true};
const redub = {id: 'v2n', row: 'a1', at: BLOCKS[1][0], to: BLOCKS[1][1], wave: [21, 'speech', 1.7], off: true};
export const LINES = [
    [['I have been waiting here for you to come back.', '我一直在这里等你回来。']],
    [['Let us talk about this tomorrow.', '这件事我们明天再说。'], ['tomorrow', '明天'], ['now', '现在']],
    [['The rain seems to be letting up.', '雨好像小了。']],
];

export const PAGES = [
    {id: 'overview', title: ['0.5.0: A fully upgraded sound system', '0.5.0 声音系统全面升级'], length: 11000, height: 400, list: [
        ['Separate tracks: dialogue, music, ambience', '分离音轨：人声、音乐、环境声'], ['Replace music: from a text description', '替换配乐：根据文字描述重新生成'],
        ['Add music: generated to fit the selected length', '添加配乐：按选中的长度生成'], ['Edit lines: only changed lines are redubbed', '修改台词：只为改过的台词重新配音'],
        ["Dub: in the original speaker's voice", '配音：沿用原说话人的声音'], ['Record: onto an audio track', '录音：录制到声音轨'],
        ['Generate picture from sound', '按声音生成画面']],
    rows: [V, ...SOUND], keep: 'a3', menuItems: [['Copy', '复制'], ['Delete', '删除'], '-', ['Separate tracks', '分离音轨']],
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
        [16500, {bar: {mode: 'idle', phase: null, text: null, button: ['Generate', '生成'], ph: ['Describe this part', '描述这一段']}, select: null}]]},
];
