// A rewrite in H3's sections, as the prompt box shows it: the scene, then a "Sound:" paragraph (any music joins it).
// The section names, the summary and the retention analysis stay out of sight; `modelText` puts them back when the
// prompt is sent, so an untouched rewrite reaches the model exactly as the model wrote it.
import { CATALOG, translator, translatorFor } from './i18n.js';

const ORDERS = [
    ['subject_definitions', 'summary', 'retention_analysis', 'detailed_description', 'overall_soundscape', 'non_diegetic_music'],
    ['integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music'],
];
const SCENE = new Set(['detailed_description', 'integrated_multimodal_description']);
// The box's own words, as the page writes them. Text written on a page of another language is read back too: English,
// Chinese in both scripts, and the language of this page.
const shotText = (t, number, time, text) => t('Shot {number} ({time}): {text}', '镜头 {number}（{time}）：{text}', { number, time, text });
const soundText = (t, text) => t('Sound: {text}', '声音：{text}', { text });
const TRADITIONAL = translatorFor({'Shot {number} ({time}): {text}': '鏡頭 {number}（{time}）：{text}', 'Sound: {text}': '聲音：{text}'}, true);
const WRITERS = [translatorFor(null, false), translatorFor(null, true), TRADITIONAL, ...(CATALOG ? [translator(true), translator(false)] : [])];
const NO_MUSIC = /^n\/?a\.?$/i;
// "[Shot 2] At 00:03.000, ..." in the model's words; "Shot 2 (0:03): ..." or "镜头 2（0:03）：..." in the box.
const AT = /^At (\d{1,2}):(\d{2}(?:\.\d+)?),[ \t]*/;
const SHOT_HEAD = /^(?:Shot|镜头|鏡頭) (?:\d+)(?: ?[(（](?<m>\d{1,2}):(?<s>\d{2}(?:\.\d+)?)[)）])?[：:][ \t]*/;
// What this page's language file writes in front of {text}, as a pattern; null when the file has no such text or writes
// something after it.
function ownHead(written, start) {
    if (!CATALOG || !written.endsWith('{text}')) return null;
    const pattern = written.slice(0, -'{text}'.length).trimEnd().replace(/[.*+?^$()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s*')
        .replace('{number}', '\\d+').replace('{time}', '(?<m>\\d{1,2}):(?<s>\\d{2}(?:\\.\\d+)?)');
    return pattern ? `${start}${pattern}[ \\t]*` : null;
}
const OWN_SHOT = ownHead(translator(false)('Shot {number} ({time}): {text}', '镜头 {number}（{time}）：{text}'), '^');
const OWN_SOUND = ownHead(translator(false)('Sound: {text}', '声音：{text}'), '');
const ownShot = OWN_SHOT && new RegExp(OWN_SHOT), shotHead = paragraph => SHOT_HEAD.exec(paragraph) || (ownShot ? ownShot.exec(paragraph) : null);
const SOUND_HEAD = new RegExp(`(?:^|\\n\\n+)(?:(?:Sound:|声音：|聲音：)[ \\t]*${OWN_SOUND ? `|${OWN_SOUND}` : ''})`, 'g');

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
export function shownRewrite(text, language = 'en', t = translator(language === 'zh')) {
    const rows = sections(text);
    if (!rows) return null;
    const get = name => rows.find(([key]) => key === name)?.[1] ?? '';
    const list = shots(rows.find(([key]) => SCENE.has(key))[1]);
    if (!list) return null;
    const scene = list.length === 1 ? list[0].text
        : list.map((shot, index) => shotText(t, index + 1, clock(shot.minutes, shot.seconds), shot.text)).join('\n\n');
    const music = get('non_diegetic_music');
    const sound = [get('overall_soundscape'), NO_MUSIC.test(music) ? '' : music].filter(Boolean).join(' ');
    return scene + '\n\n' + soundText(t, sound);
}

// The scene section for the scene part of an edited box. Paragraphs that open with a shot number become that shot,
// keeping the model's own time when the shown one is unchanged; anything before the first is the first shot.
function sceneFromBox(box, original) {
    const shot = [];
    for (const paragraph of box.split(/\n\n+/)) {
        const head = shotHead(paragraph);
        if (head || !shot.length) shot.push({head, text: paragraph.slice(head ? head[0].length : 0).trim()});
        else shot[shot.length - 1].text += ' ' + paragraph.trim();
    }
    if (shot.length === 1 && !shot[0].head) return '[Shot 1] ' + shot[0].text;
    return shot.map(({head, text}, index) => {
        const old = original?.[index];
        let at = '';
        const { m, s } = head?.groups || {};
        if (m !== undefined) {
            const time = clock(Number(m), s);
            at = old && clock(old.minutes, old.seconds) === time ? old.at
                : time === '0:00' && index === 0 ? '' : `At ${m.padStart(2, '0')}:${s},`;
        } else if (old?.at) at = old.at;
        return `[Shot ${index + 1}] ${at ? at + ' ' : ''}${text}`;
    }).join(' ');
}

// The text sent to the model for what the box holds, given the rewrite it was shown from. An edited box keeps the hidden
// definitions and retention analysis but drops the summary, which may no longer match the edited scene.
export function modelText(shown, rewrite) {
    const rows = sections(rewrite);
    if (!rows || WRITERS.some(t => shownRewrite(rewrite, 'en', t) === shown)) return rows ? rewrite : shown;
    const get = name => rows.find(([key]) => key === name)?.[1] ?? '';
    const [sceneName, sceneText] = rows.find(([key]) => SCENE.has(key));
    const box = String(shown ?? '').trim();
    // The last paragraph that opens with "Sound:" or "声音：" (in any language above) is the sound; the music goes on while
    // its words still end it. Without one, the whole box is the scene and no sound is described, as the box shows.
    let scene = box, soundscape = 'N/A', music = get('non_diegetic_music');
    const heads = [...box.matchAll(SOUND_HEAD)];
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

// The prompt widget's text for the request: text the box shows in short (a rewrite, or H3 text put in the box from
// elsewhere) goes back in the model's sections while the widget still holds what the box last recorded.
export function requestText(text, versions) {
    const side = versions?.selected === 'rewritten' ? 'rewritten' : 'original';
    const model = side === 'rewritten' ? versions?.model : versions?.originalModel;
    return model && versions[side] === text ? modelText(text, model) : text;
}
