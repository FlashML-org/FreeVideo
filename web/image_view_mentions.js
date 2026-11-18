// Reference pictures named in the prompt. Typing "@" lists the reference pictures; choosing one writes "@图1"
// ("@Image1"): the engine's Picture 1. A mention is bound to its picture, not to the number: when pictures are put in
// front or taken out, the numbers in the text follow them, and a mention whose picture was taken out reads "@图?"
// and is marked until it is chosen again. The marks are drawn over the box (the text stays the box's own).
import { listenDialogKey } from './dialog_keys.js';
import { el, t, icon } from './image_view_ui.js';

const TOKEN = /@(?:图|Image) ?(\d+|\?)/g;
export const mentionText = n => t(`@Image${n}`, `@图${n}`);

// The text with each mention renumbered from `before` (the pictures as they were) to `after` (as they are now).
// `caret` is moved by the change in length of the mentions in front of it.
export function renumberMentions(text, before, after, caret = 0) {
    let shift = 0;
    const next = text.replace(TOKEN, (whole, n, at) => {
        if (n === '?') return whole;
        const picture = before[Number(n) - 1];
        const k = picture ? after.indexOf(picture) : -1;
        const written = whole.replace(/(\d+)$/, k >= 0 ? String(k + 1) : '?');
        if (at + whole.length <= caret) shift += written.length - whole.length;
        return written;
    });
    return {text: next, caret: caret + shift, changed: next !== text};
}

// What the engine reads: each mention as <Picture N>; one without a picture is left out.
export function mentionsToEngine(text, count) {
    return text.replace(TOKEN, (_, n) => n !== '?' && Number(n) >= 1 && Number(n) <= count ? `<Picture ${n}>` : '').replace(/ {2,}/g, ' ');
}
// A picture drawn as a sketch (image editor's sketch mode) is a plan of the picture, not its look: each thing drawn in it
// is the real thing it stands for, in the same place, size, direction and pose. The words sent around it are plain
// affirmative sentences (the models follow what is said, and a "do not" can bring about the very thing it names), add
// no style of their own, and name no kind of thing the user did not name. Measured one picture at a time on the team
// engine, both models, eight sketches (a house and a tree, a cup and an apple, a street in perspective, a cat, a car,
// a person by a table, one person with raised arms, two people face to face):
// - Both models add whatever the words name: "a drawn person is a real person" or "any stick figure is a person" put
//   people (or stick figures) into an empty street. So people are named only when the description names them; then
//   its stick figures are said to be those real people.
// - Without words naming people, a stick figure stays a stick figure (a sculpture of one): what is drawn is known only
//   from the description, and the picture page asks for one beside a sketch (image_view.js).
// - "the scene in the sketch" as the subject came back as the drawing itself; "real things in a real place" does not.
// - H3: a description in front of the layout words (a Chinese one above all) made figurines or cartoons, or turned the
//   English after it into a caption; after them it is followed. A description that names a look of its own (cartoon,
//   watercolour, ...) leads instead, with short layout words, and the picture takes its look.
// - Qwen redraws its input: it keeps the plain paper and the look of simple lines unless the whole frame is said to be
//   filled and what is drawn is said to become real things as they are in life; it reads the description first.
// `numbers`: the sketches' places (1-based) in the reference list; `text`: the engine text with <Picture N>.
const STYLE_WORDS = /卡通|动漫|动画|二次元|漫画|插画|手绘|水彩|油画|国画|水墨|素描|线稿|像素|剪纸|黏土|粘土|三维|渲染|Q版|赛博|风格|画风|cartoon|anime|manga|comic|illustrat|watercolou?r|oil paint|ink wash|pixel art|claymation|\b3d\b|render|chibi|style/i;
// A description that names people (a stick figure then stands for them).
const PEOPLE_WORDS = /人|男|女|孩|童|他|她|爸|妈|爷|奶|哥|姐|弟|妹|夫|妻|侣|娃|宝宝|士兵|学生|老师|医生|警察|运动员|舞者|\b(?:people|persons?|man|men|woman|women|girls?|boys?|child|children|kids?|baby|babies|couple|family|friends?|players?|dancers?|soldiers?|students?|someone|he|she|they)\b/i;
const SKETCH_LEAD = /^\s*(?:按\s*<Picture (\d+)>\s*的构图[，,]?|Following the layout of <Picture (\d+)>,?)\s*/;
const REAL = 'Real things in a real place';
// Whether a prompt holds a description beside its sketch (more than the layout words the page writes).
export const sketchDescribed = text => !!String(text || '').replace(SKETCH_LEAD, '').replace(/<Picture \d+>/g, '').trim();
export function sketchPrompt(text, numbers, model) {
    if (!numbers?.length) return text;
    const desc = String(text || '').replace(SKETCH_LEAD, '').trim().replace(/[。.]+$/, '');
    const pics = numbers.map(n => `<Picture ${n}>`).join(' and '), many = numbers.length > 1, them = many ? 'them' : 'it', its = many ? 'their' : 'its';
    const people = desc && PEOPLE_WORDS.test(desc);
    const style = desc ? ' The style follows the description.' : '';
    if (model === 'qwen-image-2.1-turbo') return `Redraw ${pics} as a finished, fully coloured and detailed image that fills the whole frame with a complete scene. ${desc || REAL}. `
        + `${pics} ${many ? 'are layout sketches' : 'is a layout sketch'} drawn with simple lines; ${many ? 'they show' : 'it shows'} where each thing goes, how big it is and which way it faces. `
        + `Each thing drawn in ${them} becomes the real thing it stands for, whole and natural as it is in life, in its place, size, direction and pose`
        + (people ? `, and ${its} stick figures become the real people of the description, each with a whole natural body, a face, hair and clothing` : '') + `.${style} `
        + 'Every part of the picture, from edge to edge, shows the scene around and behind them: its ground, its surroundings and its light.';
    if (desc && STYLE_WORDS.test(desc)) return `${desc}. A finished, fully coloured and detailed image. The composition, places, sizes and poses match ${pics}; `
        + `each thing drawn in ${them} is the thing it stands for${people ? `, and ${its} stick figures are the people of the description` : ''}. `
        + 'The style follows the description. The background is a complete scene that fits the content.';
    const real = `${REAL}. A finished, fully coloured and detailed image. ${pics} ${many ? 'are rough plans' : 'is a rough plan'} of the picture: `
        + `each thing drawn in ${them} is the real thing it stands for, whole and natural, in the same place, size, direction and pose`
        + (people ? `, and ${its} stick figures are the real people of the description, each with a natural body, face, hair and clothing` : '') + '. '
        + 'The whole picture is a real scene, its ground, its surroundings and its light from edge to edge.';
    return desc ? `${real} ${desc}${/[\u3400-\u9fff]$/.test(desc) ? '。' : '.'}` : real;
}
// A sketch with mannequins (the sketch editor's pose tool) is line art of people in a pose, seen from a camera. Its words
// were settled over nine rounds on the team engine (Qwen-Image 2.1, two seeds, twenty pictures: every pose matched and
// every picture was in a real place, also when the description named none): the description first, then which way each
// person faces and how much of them the frame holds (`words`, written by the pose tool from the scene), then what the
// line art stands for, then real people in a real place. All affirmative; no clothes are added (the description names
// them). A description that names a look of its own (cartoon, watercolour, ...) keeps that look instead of a photograph.
// `n`: the line art's place (1-based) among the pictures; `words`: {facing, framing, angle}.
const POSE_SKETCH = n => `<Picture ${n}>是姿势草图，线条人偶表示人物的动作：头、躯干、手臂和腿的位置与朝向，人物在画面中的位置和大小，以及镜头角度，都与<Picture ${n}>一致。`;
const POSE_REAL = '画面中是真实的人，脸是完整自然的真人面孔，体型和身体比例自然，长相、发型和衣着按文字描述和场景来画。'
    + '照片在真实的地点拍摄：人物周围是与描述相符的真实环境，前景和背景都有具体的景物，画面有空间纵深，环境细节丰富，光线自然，写实摄影。'
    + '构图随意自然，像广角镜头随手抓拍的一张写实照片：人物神态放松，视线看向画面外侧方，衣料有自然的褶皱，衣角和发丝被环境风轻轻带动，带一点动作中的轻微动态，光线是侧逆光或暖色的自然环境光。';
// A description that is empty or names only the person (no place) leaves the place open: one of a few plain settings is
// named, by the seed, so pictures do not all land in the same street. A description with a place is left alone.
const POSE_PLACES = ['白天的城市街道，路面干净，两旁有树和店铺', '公园里的林荫小路，草地和树木在背后', '海边的木栈道，远处是海面和天空', '车站的站台，列车和立柱在背后', '咖啡馆的露台，桌椅和绿植在身旁', '开阔的广场，远处有楼房和行人'];
const PLACE_WORDS = /家|沙发|床|雪|草|沙|水|窗|屋顶|街|路|巷|城|市|园|山|海|湖|河|岸|滩|林|森|田|野|原|漠|岛|桥|楼|厦|房|屋|室|厅|厨|院|店|馆|场|站|台|港|港口|车|机|校|教|堂|庙|寺|塔|宫|殿|景|环境|背景|地点|户外|室内|天空|夜晚|街景|\b(?:street|road|city|town|park|garden|beach|sea|ocean|lake|river|forest|mountain|field|desert|island|bridge|building|room|house|kitchen|cafe|caf[eé]|shop|store|station|airport|school|church|temple|stage|studio|office|indoors?|outdoors?|background|scene|sky|night|rooftop|alley|square|market|gym|pool)\b/i;
const placeFor = (desc, seed) => (desc.length <= 40 && !PLACE_WORDS.test(desc.replace(/<Picture \d+>/g, '')) && !(/[a-z]/i.test(desc) && !/[\u3400-\u9fff]/.test(desc))
    ? POSE_PLACES[Math.abs(Math.floor(Number(seed) || 0)) % POSE_PLACES.length] : '');
const POSE_STYLED = '人物的长相、发型和衣着按文字描述来画，画面的风格按文字描述，背景是与描述相符的完整场景。';
// Who the mannequins are: a description that names no man or woman gets one sentence naming each mannequin's body, left
// to right ("画面中从左到右依次是一位女性、一位男性"; one person: "画面中是一位女性"), in English after an English description.
// A description that names a sex, or a number of people other than the mannequins', is left to say it alone.
const GENDER_WORDS = /[男女她爸妈哥姐弟妹妻叔姨媳]|他(?!们)|爷爷|奶奶|大爷|老爷|丈夫|夫妇|夫人|新郎|新娘|姑娘|婆婆|老婆|外婆|先生|太太|小姐|王子|公主|\b(?:m[ae]n|wom[ae]n|male|female|girls?|boys?|lad(?:y|ies)|gentlem[ae]n|guys?|gals?|he|him|his|she|hers?|husbands?|wi(?:fe|ves)|mothers?|fathers?|mom|mum|dad|sons?|daughters?|brothers?|sisters?|brides?|grooms?|kings?|queens?|princes?|princess(?:es)?|actress(?:es)?|waitress(?:es)?|aunts?|uncles?|grandmas?|grandpas?)\b/i;
const CN_DIGITS = {一: 1, 两: 2, 二: 2, 俩: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10};
const EN_DIGITS = {one: 1, a: 1, an: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10};
const PEOPLE_CN = '人|朋友|孩子|小孩|学生|同学|伙伴|舞者|运动员|士兵|游客|路人|老人|年轻人|青年|人物';
const PEOPLE_EN = 'people|persons?|friends?|kids?|children|child|students?|dancers?|players?|soldiers?|tourists?|figures?|teens?|teenagers?|adults?|someone|somebody|individuals?';
// The number of people the description names (null: none named).
export function namedPeople(text) {
    let total = null;
    const add = k => { total = (total || 0) + k; };
    for (const m of text.matchAll(new RegExp(`(\\d+|[一两二俩三四五六七八九十])\\s*(?:(?:个|名)\\s*(?:${PEOPLE_CN})|位|人)`, 'g'))) add(Number(m[1]) || CN_DIGITS[m[1]]);
    for (const m of text.matchAll(/(?:一对|情侣|双人)/g)) add(2);
    for (const m of text.matchAll(new RegExp(`\\b(\\d+|one|an?|two|three|four|five|six|seven|eight|nine|ten)\\s+(?:\\w+\\s+)?(?:${PEOPLE_EN})\\b`, 'gi'))) add(Number(m[1]) || EN_DIGITS[m[1].toLowerCase()]);
    for (const m of text.matchAll(/\b(?:a couple|a pair)\b/gi)) add(2);
    return total;
}
export function genderSentence(text, genders) {
    if (!Array.isArray(genders) || !genders.length || genders.some(g => g !== 'male' && g !== 'female')) return '';
    const named = namedPeople(text);
    if (GENDER_WORDS.test(text) || (named !== null && named !== genders.length)) return '';
    if (/[a-z]/i.test(text) && !/[\u3400-\u9fff]/.test(text)) {
        const list = genders.map(g => (g === 'male' ? 'a man' : 'a woman'));
        return list.length === 1 ? `The picture shows ${list[0]}.` : `From left to right, the picture shows ${list.slice(0, -1).join(', ')} and ${list.at(-1)}.`;
    }
    const list = genders.map(g => (g === 'male' ? '一位男性' : '一位女性'));
    return list.length === 1 ? `画面中是${list[0]}。` : `画面中从左到右依次是${list.join('、')}。`;
}
export function posePrompt(text, n, words, seed) {
    const desc = String(text || '').replace(SKETCH_LEAD, '').trim().replace(/[。.\s]+$/, '');
    const lead = [words?.facing, words?.framing, words?.angle].filter(w => typeof w === 'string' && w.trim()).join('，');
    const close = /[\u3400-\u9fff]$/.test(desc) ? '。' : '. ';
    const who = genderSentence(desc.replace(/<Picture \d+>/g, ''), words?.genders);
    const place = desc && STYLE_WORDS.test(desc) ? '' : placeFor(desc, seed);
    return `${desc ? desc + close : ''}${who ? who + (who.endsWith('.') ? ' ' : '') : ''}${lead ? lead + '。' : ''}${place ? `场景是${place}。` : ''}${POSE_SKETCH(n)}${words?.arms ? words.arms + '。' : ''}${desc && STYLE_WORDS.test(desc) ? POSE_STYLED : POSE_REAL}`;
}
export const brokenMentions = (text, count) => [...text.matchAll(TOKEN)].filter(([, n]) => n === '?' || Number(n) < 1 || Number(n) > count).length;
export const hasMentions = text => new RegExp(TOKEN.source).test(text);

// The marks over the box and the "@" list. pictures(): [{url, label}] in order; onAdd(anchor): the way to add one.
export function promptMentions({prompt, host, pictures, onAdd, onPick = () => {}}) {
    const marks = el('div', 'fv-iv-marks'), sheet = el('div', 'fv-iv-marks-text'), caretMark = el('span', 'fv-iv-marks-caret');
    marks.setAttribute('aria-hidden', 'true'); marks.append(sheet);
    prompt.after(marks);
    const list = el('div', 'fv-iv-at fv-glass fv-g3'); list.setAttribute('role', 'listbox'); list.hidden = true;
    list.setAttribute('aria-label', t('Reference pictures', '参考图'));
    host.append(list);
    let open = null; // {start, active, items}
    const COPY = ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing', 'lineHeight', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'textIndent', 'wordSpacing', 'tabSize'];

    function paint() {
        const cs = getComputedStyle(prompt);
        for (const k of COPY) sheet.style[k] = cs[k];
        marks.style.left = `${prompt.offsetLeft + prompt.clientLeft}px`; marks.style.top = `${prompt.offsetTop + prompt.clientTop}px`;
        marks.style.width = `${prompt.clientWidth}px`; marks.style.height = `${prompt.clientHeight}px`;
        const text = prompt.value, count = pictures().length, caret = prompt.selectionStart ?? text.length;
        const parts = []; let last = 0, caretPlaced = false;
        const plain = (from, to) => {
            if (!caretPlaced && caret >= from && caret <= to) { parts.push(document.createTextNode(text.slice(from, caret)), caretMark, document.createTextNode(text.slice(caret, to))); caretPlaced = true; }
            else parts.push(document.createTextNode(text.slice(from, to)));
        };
        for (const m of text.matchAll(TOKEN)) {
            plain(last, m.index);
            const mark = el('mark', 'fv-iv-mark', m[0]);
            if (m[1] === '?' || Number(m[1]) > count) mark.dataset.broken = 'true';
            parts.push(mark); last = m.index + m[0].length;
        }
        plain(last, text.length);
        if (!caretPlaced) parts.push(caretMark);
        // A last empty line still has its height in the box.
        parts.push(document.createTextNode('​'));
        sheet.replaceChildren(...parts);
        sheet.style.transform = `translateY(${-prompt.scrollTop}px)`;
        // While the list is open the marks stay laid out: the list is placed at the "@".
        marks.hidden = !hasMentions(text) && !open;
        if (open) place();
    }

    // The list sits under the "@", inside the box's own left and right edges (moved left when the "@" is near the right).
    function place() {
        const box = marks.getBoundingClientRect(), r = caretMark.getBoundingClientRect(), edge = prompt.getBoundingClientRect();
        const line = parseFloat(getComputedStyle(prompt).lineHeight) || 20, width = Math.min(240, edge.width, innerWidth - 32);
        list.style.width = `${width}px`;
        const left = Math.max(edge.left, Math.min(edge.right - width, (r.left || box.left) - 12));
        let top = Math.min(box.bottom, (r.top || box.top) + line) + 4;
        const height = list.offsetHeight || 200;
        if (top + height > innerHeight - 16) top = Math.max(16, (r.top || box.top) - height - 4);
        list.style.left = `${Math.round(left)}px`; list.style.top = `${Math.round(top)}px`;
    }

    function items(query) {
        const all = pictures().map((p, at) => ({n: at + 1, ...p}));
        const q = query.replace(/^(图|image)/i, '').trim();
        return q ? all.filter(p => String(p.n).startsWith(q)) : all;
    }
    function draw() {
        const rows = open.items.map((p, k) => {
            const b = el('button', 'fv-iv-at-item'); b.type = 'button'; b.setAttribute('role', 'option'); b.tabIndex = -1;
            b.setAttribute('aria-selected', String(k === open.active));
            const thumb = el('img'); thumb.src = p.url; thumb.alt = '';
            b.append(thumb, el('span', 'fv-iv-at-name', p.label), el('span', 'fv-iv-at-tag', mentionText(p.n)));
            b.addEventListener('pointerdown', event => event.preventDefault());
            b.addEventListener('click', () => choose(p.n));
            return b;
        });
        if (!rows.length) {
            const empty = el('p', 'fv-iv-at-empty', pictures().length ? t('No picture with that number', '没有这个编号的参考图') : t('No reference pictures yet', '还没有参考图'));
            const add = el('button', 'fv-iv-at-item fv-iv-at-add'); add.type = 'button'; add.setAttribute('role', 'option'); add.tabIndex = -1;
            add.append(icon('plus'), el('span', 'fv-iv-at-name', t('Add a reference picture', '添加参考图')));
            add.setAttribute('aria-selected', 'true');
            add.addEventListener('pointerdown', event => event.preventDefault());
            add.addEventListener('click', () => { const at = open?.start; close(); if (at != null) removeAt(at); onAdd(prompt); });
            rows.push(empty, add);
        }
        list.replaceChildren(...rows);
        list.querySelector('[aria-selected=true]')?.scrollIntoView({block: 'nearest'});
    }
    function removeAt(start) {
        // The lone "@" that opened the list goes with it when the list leads elsewhere.
        if (prompt.value[start] === '@') { prompt.setRangeText('', start, Math.max(start + 1, prompt.selectionStart ?? start + 1), 'end'); prompt.dispatchEvent(new Event('input', {bubbles: true})); }
    }
    function openAt(start) {
        open = {start, active: 0, items: []};
        list.hidden = false; refresh();
    }
    function refresh() {
        if (!open) return;
        const caret = prompt.selectionStart ?? 0, query = prompt.value.slice(open.start + 1, caret);
        if (caret <= open.start || prompt.value[open.start] !== '@' || /\s/.test(query) || query.length > 8) { close(); return; }
        open.items = items(query); open.active = Math.min(open.active, Math.max(0, open.items.length - 1));
        draw(); paint();
    }
    function close() { if (!open) return; open = null; list.hidden = true; list.replaceChildren(); }
    function choose(n) {
        if (!open) return;
        const start = open.start, end = prompt.selectionStart ?? start + 1;
        close();
        // The text after it stays as it is; only a digit right after would read as part of the number.
        const after = /\d/.test(prompt.value[end] || '') ? ' ' : '';
        prompt.setRangeText(mentionText(n) + after, start, end, 'end');
        prompt.focus();
        prompt.dispatchEvent(new Event('input', {bubbles: true}));
        onPick(n);
    }

    const onInput = event => {
        if (open) { refresh(); return; }
        const caret = prompt.selectionStart ?? 0;
        // "@" after anything (a letter, a Chinese character, a digit, a space or the start) opens the list.
        if (event?.inputType?.startsWith('insert') && prompt.value[caret - 1] === '@') openAt(caret - 1);
        else paint();
    };
    prompt.addEventListener('input', onInput);
    prompt.addEventListener('scroll', () => { sheet.style.transform = `translateY(${-prompt.scrollTop}px)`; if (open) place(); }, {passive: true});
    prompt.addEventListener('click', () => { if (open) refresh(); });
    prompt.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== prompt) close(); }, 0));
    listenDialogKey(prompt, 'keydown', event => {
        if (!open) return;
        const n = open.items.length;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault(); if (!n) return;
            open.active = (open.active + (event.key === 'ArrowDown' ? 1 : n - 1)) % n; draw();
        } else if (event.key === 'Enter' || event.key === 'Tab') {
            event.preventDefault();
            if (n) choose(open.items[open.active].n); else list.querySelector('.fv-iv-at-add')?.click();
        } else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
        else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight' || event.key === 'Home' || event.key === 'End') setTimeout(refresh, 0);
    });
    const watch = typeof ResizeObserver === 'function' ? new ResizeObserver(() => paint()) : null; watch?.observe(prompt);
    const outside = event => { if (open && !list.contains(event.target) && event.target !== prompt) close(); };
    document.addEventListener('pointerdown', outside, true);
    return {paint, close, isOpen: () => !!open, element: list,
        dispose() { watch?.disconnect(); document.removeEventListener('pointerdown', outside, true); list.remove(); marks.remove(); }};
}
