// Director checklist: fill H3 sections without Autopilot. Autopilot / FreeToken can still rewrite.

export const LANGUAGES = ['English', 'Chinese', 'Spanish', 'Japanese'];

export function emptyDraft() {
    return {
        placeKind: '', place: '', time: '', weather: '', lighting: '',
        style: 'cinematic', shot: 'medium-wide', motion: '', motionSpeed: 'slow',
        action: '', sounds: '', music: '', captions: 'none',
        characters: [emptyCharacter()],
    };
}

export function emptyCharacter() {
    return {who: '', outfit: '', mood: '', line: '', language: 'English'};
}

export function filledChecks(draft) {
    const people = (draft.characters || []).filter(row => String(row.who || '').trim());
    const spoken = people.some(row => String(row.line || '').trim());
    return {
        place: Boolean(draft.placeKind || String(draft.place || '').trim()),
        time: Boolean(draft.time),
        weather: Boolean(draft.weather) || draft.placeKind === 'inside',
        lighting: Boolean(draft.lighting),
        camera: Boolean(draft.motion && draft.shot),
        character: people.length > 0,
        outfit: people.some(row => String(row.outfit || '').trim()),
        mood: people.some(row => String(row.mood || '').trim()),
        dialog: spoken,
        action: Boolean(String(draft.action || '').trim()),
        sounds: Boolean(String(draft.sounds || '').trim()),
        music: Boolean(String(draft.music || '').trim()),
        captions: draft.captions === 'none',
    };
}

function clause(parts) {
    return parts.map(part => String(part || '').replace(/\s+/g, ' ').trim()).filter(Boolean).join(' ');
}

function cameraSentence(draft) {
    const speed = draft.motionSpeed === 'fast' ? 'at fast speed' : draft.motionSpeed === 'moderate' ? 'at moderate speed' : 'at slow speed';
    const motion = {
        static: 'The camera holds a static shot.',
        'push-in': `The camera pushes in with small amplitude ${speed}.`,
        'pull-out': `The camera pulls out with small amplitude ${speed}.`,
        pan: `The camera pans with small amplitude ${speed}.`,
        truck: `The camera trucks with small amplitude ${speed}.`,
        tilt: `The camera tilts with small amplitude ${speed}.`,
        tracking: `The camera tracks the subject ${speed}.`,
        arc: `The camera arcs around the subject with small amplitude ${speed}.`,
        zoom: `The camera zooms with small amplitude ${speed}.`,
        pov: 'The camera holds a point-of-view shot.',
    }[draft.motion];
    return motion || '';
}

function setting(draft) {
    const kind = draft.placeKind === 'inside' ? 'an interior' : draft.placeKind === 'outside' ? 'an exterior' : '';
    const named = String(draft.place || '').trim();
    const where = named ? (kind ? `${kind} ${named}` : named) : kind || 'the scene';
    const when = draft.time ? `in the ${draft.time}` : '';
    const sky = draft.placeKind === 'inside' ? '' : draft.weather ? `The weather is ${draft.weather}.` : '';
    const light = draft.lighting ? `Lighting is ${draft.lighting}.` : '';
    return {where, when, sky, light};
}

export function composePrompt(draft) {
    const style = draft.style === 'live-action' ? 'Live-action' : `Live-action, ${draft.style || 'cinematic'}`;
    const shot = draft.shot || 'medium-wide';
    const scene = setting(draft);
    const people = (draft.characters || []).filter(row => String(row.who || '').trim());
    const cast = people.map((row, index) => {
        const id = `S${index + 1}`;
        const who = String(row.who).trim();
        const outfit = String(row.outfit || '').trim();
        const mood = String(row.mood || '').trim();
        const line = String(row.line || '').trim();
        const language = LANGUAGES.includes(row.language) ? row.language : (String(row.language || '').trim() || 'English');
        const body = clause([
            `${who} (${id})`,
            outfit ? `wearing ${outfit}` : '',
            mood ? `looking ${mood}` : '',
        ]) + '.';
        const speech = line ? ` ${who} (${id}) says: <d>[${language}] ${line}</d>` : '';
        return {body, speech};
    });
    const action = String(draft.action || '').trim();
    const description = clause([
        `[Shot 1] ${style}, a ${shot} shot frames ${scene.where}${scene.when ? ' ' + scene.when : ''}.`,
        scene.sky, scene.light,
        ...cast.map(row => row.body),
        action ? `${action.replace(/[.]+$/, '')}.` : '',
        cameraSentence(draft),
        ...cast.map(row => row.speech.trim()),
        draft.captions === 'none' ? 'No subtitles, no on-screen text.' : '',
    ]);
    const sounds = String(draft.sounds || '').trim() || 'N/A';
    const music = String(draft.music || '').trim() || 'N/A';
    return [
        `integrated_multimodal_description: ${description}`,
        `overall_soundscape: ${sounds}`,
        `non_diegetic_music: ${music}`,
    ].join('\n\n');
}

function chipRow(t, options, current, onPick) {
    const row = document.createElement('div');
    row.className = 'fv-wizard-chips';
    row.setAttribute('role', 'group');
    for (const [value, en, zh] of options) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'fv-wizard-chip';
        button.textContent = t(en, zh);
        button.setAttribute('aria-pressed', String(current === value));
        button.onclick = () => onPick(current === value ? '' : value);
        row.append(button);
    }
    return row;
}

function field(title, control) {
    const wrap = document.createElement('div');
    wrap.className = 'fv-wizard-field';
    const label = document.createElement('span');
    label.textContent = title;
    wrap.append(label, control);
    return wrap;
}

function input(value, placeholder, onChange) {
    const node = document.createElement('input');
    node.type = 'text';
    node.value = value;
    node.placeholder = placeholder;
    node.oninput = () => onChange(node.value);
    return node;
}

export function createPromptWizard({t, prompt, onWrite = () => {}, animate}) {
    const el = (tag, cls, text) => {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text != null) node.textContent = text;
        return node;
    };
    let draft = emptyDraft();
    const wrap = el('details', 'fv-prompt-wizard');
    const summary = el('summary', null, t('Director checklist', '导演清单'));
    wrap.append(summary);
    const body = el('div', 'fv-wizard-body');
    body.append(el('p', null, t(
        'Fill the shot yourself. This writes MiniMax H3 sections (description, sound, music) with camera prose and <d> dialogue. Autopilot is the hands-off path; FreeToken can still rewrite after you apply.',
        '自己填这一镜。会写成 MiniMax H3 的三个段落（画面、环境声、配乐），含镜头用语和 <d> 对白。不想自己写就用 Autopilot；写好后仍可用 FreeToken 改写。')));
    const missing = el('p', 'fv-wizard-missing');
    missing.setAttribute('role', 'status');
    const mount = el('div', 'fv-wizard-form');
    const actions = el('div', 'fv-wizard-actions');
    const apply = document.createElement('button');
    apply.type = 'button'; apply.className = 'fv-quiet';
    apply.textContent = t('Write into prompt', '写入提示词');
    const clear = document.createElement('button');
    clear.type = 'button'; clear.className = 'fv-quiet';
    clear.textContent = t('Clear checklist', '清空清单');
    actions.append(apply, clear);
    body.append(missing, mount, actions);
    wrap.append(body);
    const motion = animate?.(wrap);

    const names = {
        place: t('Place', '地点'), time: t('Time of day', '时间'), weather: t('Weather', '天气'),
        lighting: t('Lighting', '光线'), camera: t('Camera', '镜头'), character: t('Character', '人物'),
        outfit: t('Outfit', '服装'), mood: t('Expression / mood', '表情 / 情绪'), dialog: t('Dialogue', '对白'),
        action: t('Action', '动作'), sounds: t('Sounds', '音效'), music: t('Music', '配乐'),
        captions: t('No captions', '无字幕'),
    };

    function note() {
        const checks = filledChecks(draft);
        const undone = Object.entries(checks).filter(([, ok]) => !ok).map(([id]) => names[id]);
        const done = Object.values(checks).filter(Boolean).length;
        const total = Object.keys(checks).length;
        summary.textContent = t(`Director checklist · ${done}/${total}`, `导演清单 · ${done}/${total}`);
        missing.textContent = undone.length
            ? t(`Still open: ${undone.join(', ')}. You can write anyway.`, `还未填：${undone.join('、')}。仍可写入。`)
            : t('Checklist complete. Write into the prompt, then edit the textarea if you need to.',
                '清单已齐。写入提示词后，仍可在文本框里改。');
        missing.dataset.ready = String(undone.length === 0);
        const occupied = Boolean(String(prompt?.value || '').trim());
        apply.textContent = occupied
            ? t('Replace prompt', '替换提示词')
            : t('Write into prompt', '写入提示词');
        apply.disabled = !(checks.place || checks.character || checks.action);
    }

    function paint() {
        note();
        mount.replaceChildren();
        mount.append(
            field(t('Place', '地点'), chipRow(t, [
                ['outside', 'Outside', '室外'], ['inside', 'Inside', '室内'],
            ], draft.placeKind, value => { draft.placeKind = value; paint(); })),
            field(t('Where', '具体地点'), (() => {
                const stack = document.createElement('div');
                stack.className = 'fv-wizard-stack';
                stack.append(
                    chipRow(t, [
                        ['home', 'Home', '家'], ['office', 'Office', '办公室'], ['store', 'Store', '店'],
                        ['room', 'Room', '房间'], ['street', 'Street', '街'], ['park', 'Park', '公园'],
                    ], ['home', 'office', 'store', 'room', 'street', 'park'].includes(draft.place) ? draft.place : '',
                        value => { draft.place = value; paint(); }),
                    input(draft.place, t('or type: kitchen, rooftop, cafe…', '或输入：厨房、天台、咖啡馆…'),
                        value => { draft.place = value; note(); }),
                );
                return stack;
            })()),
            field(t('Time of day', '时间'), chipRow(t, [
                ['morning', 'Morning', '早晨'], ['noon', 'Noon', '正午'], ['afternoon', 'Afternoon', '下午'],
                ['golden hour', 'Golden hour', '黄金时刻'], ['night', 'Night', '夜晚'],
            ], draft.time, value => { draft.time = value; paint(); })),
            field(t('Weather', '天气'), chipRow(t, [
                ['sunny', 'Sunny', '晴'], ['cloudy', 'Cloudy', '阴'], ['rainy', 'Rainy', '雨'],
                ['windy', 'Windy', '风'], ['snowing', 'Snowing', '雪'], ['foggy', 'Fog', '雾'],
            ], draft.weather, value => { draft.weather = value; paint(); })),
            field(t('Lighting', '光线'), chipRow(t, [
                ['soft natural window light', 'Window', '窗光'], ['overcast soft light', 'Overcast', '阴天光'],
                ['hard sunlight', 'Hard sun', '硬阳光'], ['warm practical lamps', 'Lamps', '灯光'],
                ['neon', 'Neon', '霓虹'], ['moonlight', 'Moonlight', '月光'],
            ], draft.lighting, value => { draft.lighting = value; paint(); })),
            field(t('Camera style', '镜头风格'), chipRow(t, [
                ['cinematic', 'Cinematic', '电影感'], ['documentary', 'Documentary', '纪录'],
                ['handheld', 'Handheld', '手持'], ['commercial', 'Commercial', '广告'],
            ], draft.style, value => { draft.style = value || 'cinematic'; paint(); })),
            field(t('Framing', '景别'), chipRow(t, [
                ['wide', 'Wide', '全景'], ['medium-wide', 'Medium-wide', '中全'],
                ['medium', 'Medium', '中景'], ['close-up', 'Close-up', '近景'],
            ], draft.shot, value => { draft.shot = value || 'medium-wide'; paint(); })),
            field(t('Camera move', '运镜'), chipRow(t, [
                ['static', 'Static', '固定'], ['push-in', 'Push in', '推近'], ['pull-out', 'Pull out', '拉远'],
                ['pan', 'Pan', '横摇'], ['truck', 'Truck', '横移'], ['tilt', 'Tilt', '俯仰'],
                ['tracking', 'Track', '跟随'], ['arc', 'Arc', '环绕'], ['zoom', 'Zoom', '变焦'], ['pov', 'POV', '主观'],
            ], draft.motion, value => { draft.motion = value; paint(); })),
            field(t('Move speed', '运镜速度'), chipRow(t, [
                ['slow', 'Slow', '慢'], ['moderate', 'Moderate', '中'], ['fast', 'Fast', '快'],
            ], draft.motionSpeed, value => { draft.motionSpeed = value || 'slow'; paint(); })),
            field(t('What happens', '发生什么'), input(draft.action, t('One beat of action', '一句动作'), value => { draft.action = value; note(); })),
            field(t('Sounds', '音效'), input(draft.sounds, t('Ambience, footsteps, breaths… or N/A', '环境声、脚步、呼吸… 或 N/A'), value => { draft.sounds = value; note(); })),
            field(t('Music', '配乐'), input(draft.music, t('Instruments and tempo… or N/A', '乐器与节奏… 或 N/A'), value => { draft.music = value; note(); })),
            field(t('Captions', '字幕'), chipRow(t, [
                ['none', 'No subtitles', '无字幕'], ['allow', 'Allow captions', '允许字幕'],
            ], draft.captions, value => { draft.captions = value || 'none'; paint(); })),
        );
        draft.characters.forEach((row, index) => {
            const block = el('div', 'fv-wizard-cast');
            const title = el('span', 'fv-wizard-cast-title', t(`Character ${index + 1} (S${index + 1})`, `人物 ${index + 1}（S${index + 1}）`));
            const remove = document.createElement('button');
            remove.type = 'button'; remove.className = 'fv-quiet';
            remove.textContent = t('Remove', '移除');
            remove.hidden = draft.characters.length < 2;
            remove.onclick = () => { draft.characters.splice(index, 1); paint(); };
            const head = el('div', 'fv-wizard-cast-head'); head.append(title, remove);
            block.append(
                head,
                field(t('Who', '谁'), input(row.who, t('A young woman, a ginger tabby…', '年轻女子、橘猫…'), value => { row.who = value; note(); })),
                field(t('Outfit', '服装'), input(row.outfit, t('Cream hoodie, dark trousers…', '米色帽衫、深色长裤…'), value => { row.outfit = value; note(); })),
                field(t('Expression / mood', '表情 / 情绪'), input(row.mood, t('calm, smiling, tense…', '平静、微笑、紧张…'), value => { row.mood = value; note(); })),
                field(t('Spoken line', '台词'), input(row.line, t('Hi', '嗨'), value => { row.line = value; note(); })),
                field(t('Line language', '台词语言'), chipRow(t, LANGUAGES.map(name => [name, name, name]), row.language,
                    value => { row.language = value || 'English'; paint(); })),
            );
            mount.append(block);
        });
        if (draft.characters.length < 3) {
            const add = document.createElement('button');
            add.type = 'button'; add.className = 'fv-quiet';
            add.textContent = t('Add character', '添加人物');
            add.onclick = () => { draft.characters.push(emptyCharacter()); paint(); };
            mount.append(add);
        }
    }

    apply.onclick = () => {
        onWrite(composePrompt(draft));
        paint();
        prompt?.focus({preventScroll: true});
    };
    clear.onclick = () => { draft = emptyDraft(); paint(); };
    paint();
    return {element: wrap, compose: () => composePrompt(draft), dispose() { motion?.(); }};
}
