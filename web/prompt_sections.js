// A rewrite in H3's sections, as the prompt box shows it: the scene, then a "Sound:" paragraph (any music joins it).
// The section names, the summary and the retention analysis stay out of sight; `modelText` puts them back when the
// prompt is sent, so an untouched rewrite reaches the model exactly as the model wrote it.
const ORDERS = [
    ['subject_definitions', 'summary', 'retention_analysis', 'detailed_description', 'overall_soundscape', 'non_diegetic_music'],
    ['integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music'],
];
const SCENE = new Set(['detailed_description', 'integrated_multimodal_description']);
const LABELS = [['en', 'Sound: '], ['zh', '声音：']];
const NO_MUSIC = /^n\/?a\.?$/i;
// "[Shot 2] At 00:03.000, ..." in the model's words; "Shot 2 (0:03): ..." or "镜头 2（0:03）：..." in the box.
const AT = /^At (\d{1,2}):(\d{2}(?:\.\d+)?),[ \t]*/;
const SHOT_HEAD = /^(?:Shot|镜头) (\d+)(?: ?[(（](\d{1,2}):(\d{2}(?:\.\d+)?)[)）])?[：:][ \t]*/;

// [[name, body]] when the text is exactly H3's sections in order (subject definitions may be absent), else null.
function sections(text) {
    const parts = String(text ?? '').trim().split(/\n\n+(?=[a-z_]+:)/);
    const rows = parts.map(part => /^([a-z_]+):[ \t]*([\s\S]*)$/.exec(part)).map(match => match && [match[1], match[2].trim()]);
    if (!rows.length || rows.some(row => !row)) return null;
    const names = rows.map(([name]) => name).join();
    return [ORDERS[0], ORDERS[0].slice(1), ORDERS[1]].some(order => order.join() === names) ? rows : null;
}

// The scene's shots in order as {at, minutes, seconds, text} (a scene without markers is one shot), or null when the
// markers are not [Shot 1], [Shot 2], ... from the start.
function shots(scene) {
    const pieces = scene.split(/[ \t]*\[Shot (\d+)\][ \t]*/);
    if (pieces.length === 1) return [{at: '', minutes: 0, seconds: '00', text: scene.trim()}];
    if (pieces[0].trim()) return null;
    const list = [];
    for (let i = 1; i < pieces.length; i += 2) {
        if (Number(pieces[i]) !== list.length + 1) return null;
        const at = AT.exec(pieces[i + 1]);
        list.push({at: at ? at[0].trim() : '', minutes: at ? Number(at[1]) : 0, seconds: at ? at[2] : '00', text: pieces[i + 1].slice(at ? at[0].length : 0).trim()});
    }
    return list;
}

const clock = (minutes, seconds) => `${minutes}:${seconds.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')}`;

// What the box shows for a rewrite, or null to show it as it is. A single shot shows without its marker; several shots
// each start a paragraph with their number and start time.
export function shownRewrite(text, language = 'en') {
    const rows = sections(text);
    if (!rows) return null;
    const get = name => rows.find(([key]) => key === name)?.[1] ?? '';
    const list = shots(rows.find(([key]) => SCENE.has(key))[1]);
    if (!list) return null;
    const zh = language === 'zh';
    const scene = list.length === 1 ? list[0].text : list.map((shot, index) => {
        const time = clock(shot.minutes, shot.seconds);
        return (zh ? `镜头 ${index + 1}（${time}）：` : `Shot ${index + 1} (${time}): `) + shot.text;
    }).join('\n\n');
    const music = get('non_diegetic_music');
    const sound = [get('overall_soundscape'), NO_MUSIC.test(music) ? '' : music].filter(Boolean).join(' ');
    return scene + '\n\n' + (zh ? LABELS[1][1] : LABELS[0][1]) + sound;
}

// The scene section for the scene part of an edited box. Paragraphs that open with a shot number become that shot,
// keeping the model's own time when the shown one is unchanged; anything before the first is the first shot.
function sceneFromBox(box, original) {
    const shot = [];
    for (const paragraph of box.split(/\n\n+/)) {
        const head = SHOT_HEAD.exec(paragraph);
        if (head || !shot.length) shot.push({head, text: paragraph.slice(head ? head[0].length : 0).trim()});
        else shot[shot.length - 1].text += ' ' + paragraph.trim();
    }
    if (shot.length === 1 && !shot[0].head) return '[Shot 1] ' + shot[0].text;
    return shot.map(({head, text}, index) => {
        const old = original?.[index];
        let at = '';
        if (head?.[2] !== undefined) {
            const time = clock(Number(head[2]), head[3]);
            at = old && clock(old.minutes, old.seconds) === time ? old.at
                : time === '0:00' && index === 0 ? '' : `At ${head[2].padStart(2, '0')}:${head[3]},`;
        } else if (old?.at) at = old.at;
        return `[Shot ${index + 1}] ${at ? at + ' ' : ''}${text}`;
    }).join(' ');
}

// The text sent to the model for what the box holds, given the rewrite it was shown from. An edited box keeps the hidden
// definitions and retention analysis but drops the summary, which may no longer match the edited scene.
export function modelText(shown, rewrite) {
    const rows = sections(rewrite);
    if (!rows || LABELS.some(([code]) => shownRewrite(rewrite, code) === shown)) return rows ? rewrite : shown;
    const get = name => rows.find(([key]) => key === name)?.[1] ?? '';
    const [sceneName, sceneText] = rows.find(([key]) => SCENE.has(key));
    const box = String(shown ?? '').trim();
    // The last paragraph that opens with "Sound:" or "声音：" (in either language) is the sound; the music goes on while
    // its words still end it. Without one, the whole box is the scene and no sound is described, as the box shows.
    let scene = box, soundscape = 'N/A', music = get('non_diegetic_music');
    const heads = [...box.matchAll(/(?:^|\n\n+)(?:Sound:|声音：)[ \t]*/g)];
    const head = heads[heads.length - 1];
    let sound = head ? box.slice(head.index + head[0].length).trim() : '';
    if (head) scene = box.slice(0, head.index).trim();
    if (!NO_MUSIC.test(music) && sound.endsWith(music)) sound = sound.slice(0, -music.length).trim();
    else music = 'N/A';
    soundscape = sound || soundscape;
    const keep = rows.filter(([name]) => name === 'subject_definitions' || name === 'retention_analysis');
    return [...keep, [sceneName, sceneFromBox(scene, shots(sceneText))], ['overall_soundscape', soundscape], ['non_diegetic_music', music]]
        .map(([name, body]) => `${name}: ${body}`).join('\n\n');
}

// The prompt widget's text for the request: a rewrite the box shows in short goes back in the model's sections.
export function requestText(text, versions) {
    return versions?.selected === 'rewritten' && versions.model ? modelText(text, versions.model) : text;
}
