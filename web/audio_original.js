// "Reference | Use as is" for an audio card, and the length the chosen audio fixes.
// The server's preview check is the only source of that length; the client keeps
// the reply in memory so the media panel and the Studio ask once per clip.

const css = document.createElement('link');
css.rel = 'stylesheet'; css.href = new URL('./audio_original.css', import.meta.url).href; document.head.append(css);

export const AUDIO_USE = Object.freeze({reference: 'reference', original: 'original'});
export const usesOriginal = row => row?.use === AUDIO_USE.original;
export const activeOriginal = rows => rows.find(row => row.enabled !== false && usesOriginal(row)) || null;
// The same card used as a reference: without the choice and its range.
export const referenceRow = row => { if (!usesOriginal(row)) return row; const {use: _use, range: _range, ...rest} = row; return rest; };

const rangeKey = range => range ? `${range.start_sample}-${range.end_sample}` : 'all';
const keyOf = row => `${row.file}|${rangeKey(row.range)}`;
const settled = new Map(), pending = new Map();

// The reply for this clip and range, if already checked.
export const checkedLength = row => settled.get(keyOf(row)) || null;
export const forgetChecks = () => { settled.clear(); pending.clear(); };

// {ok: true, ...reply} or {ok: false, code, message, message_zh}. A network failure
// has no server sentence, so it comes back with code 'unreachable'.
export function checkOriginal(api, row) {
    const key = keyOf(row);
    if (settled.has(key)) return Promise.resolve({ok: true, ...settled.get(key)});
    if (pending.has(key)) return pending.get(key);
    const body = {file: row.file, use: AUDIO_USE.original};
    if (row.range) body.range = row.range;
    const request = (async () => {
        try {
            const response = await api.fetchApi('/freevideo/media/audio-use', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
            const reply = await response.json();
            if (!response.ok || reply.use !== AUDIO_USE.original || !(reply.seconds > 0)) {
                return {ok: false, code: reply.code || 'unreachable', message: reply.message, message_zh: reply.message_zh};
            }
            settled.set(key, reply);
            return {ok: true, ...reply};
        } catch { return {ok: false, code: 'unreachable'}; }
        finally { pending.delete(key); }
    })();
    pending.set(key, request);
    return request;
}

export function failureText(result, t) {
    if (result.message && result.message_zh) return t(result.message, result.message_zh);
    return t('This audio could not be used as is.', '无法原样使用这段音频。');
}

// Where the engine refuses Use as is (continuing a video, a first-pass preview), the Studio
// names that state here and the option is greyed out on every audio card until it ends.
const blockers = new Set();
export const originalBlocked = () => blockers.size > 0;
export function blockOriginal(reason, on) {
    const before = blockers.size > 0;
    if (on) blockers.add(reason); else blockers.delete(reason);
    if (before !== blockers.size > 0) window.dispatchEvent(new CustomEvent('freevideo-audio-blocked'));
}

export const multipleText = t => t('Choose "Use as is" for only one audio file.', '一次只能有一段音频选择“原样使用”。');

// The two-way switch on an audio card. `onPick` receives 'reference' or 'original'.
export function createUseSwitch({t, use, onPick}) {
    const group = document.createElement('div');
    group.className = 'fv-audio-use'; group.setAttribute('role', 'group');
    group.setAttribute('aria-label', t('Audio use', '音频用途'));
    const buttons = {};
    for (const [value, label] of [[AUDIO_USE.reference, t('Reference', '参考')], [AUDIO_USE.original, t('Use as is', '原样使用')]]) {
        const b = document.createElement('button'); b.type = 'button'; b.textContent = label; b.dataset.use = value;
        b.setAttribute('aria-pressed', String(value === use));
        b.onclick = () => { if (b.getAttribute('aria-pressed') !== 'true') onPick(value); };
        buttons[value] = b; group.append(b);
    }
    let chosen = use, working = false;
    const apply = () => {
        for (const [name, b] of Object.entries(buttons)) {
            b.setAttribute('aria-pressed', String(name === chosen));
            b.disabled = working || (name === AUDIO_USE.original && originalBlocked());
        }
    };
    group.set = (value, busy = false) => { chosen = value; working = busy; apply(); };
    const refresh = () => { if (group.isConnected) apply(); else window.removeEventListener('freevideo-audio-blocked', refresh); };
    window.addEventListener('freevideo-audio-blocked', refresh);
    apply();
    return group;
}

// The lock beside the duration label while the audio decides the length.
export function lockMark() {
    const mark = document.createElement('span'); mark.className = 'fv-audio-lock'; mark.hidden = true; mark.setAttribute('aria-hidden', 'true');
    mark.innerHTML = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        + '<rect x="3.5" y="7" width="9" height="6.5" rx="1.5"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/></svg>';
    return mark;
}
