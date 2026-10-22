// Replace person: a source video and a person image in the Media section.
// The state lives in the Media node's properties, so the ordinary media list is never touched
// and comes back unchanged when the option is turned off.
import { referenceKind } from './prompt_references.js';
import { uploadMedia } from './media_upload.js';
import { firstFrameRow } from './first_frame_row.js';
import { swapState, swapRows, effectiveRows, keepsSound, withPrefix, swapMissing, SWAP_LORA, SWAP_PREFIX, MIN_SOURCE_SECONDS, SWAP_KEY } from './replace_state.js';

export {swapState, swapRows, effectiveRows, keepsSound, withPrefix, swapMissing, SWAP_LORA, SWAP_PREFIX, MIN_SOURCE_SECONDS};
const KEY = SWAP_KEY;

// A first or last frame, in the media list or on a connected input, rules the option out.
export function framesPresent(media) {
    try {
        const rows = JSON.parse(media?.widgets?.find(w => w.name === 'assets')?.value || '[]');
        if (rows.some(row => row.enabled !== false && (row.role === 'first' || row.role === 'last'))) return true;
    } catch {}
    return Boolean(media?.inputs?.some(input => (input.name === 'first' || input.name === 'last') && input.link != null));
}

// What is still missing, in one line (t picks the language).
export const swapMissingText = (missing, t) => missing === 'source' ? t('Add a source video first.', '请先添加源视频。')
    : missing === 'person' ? t('Add a person image first.', '请先添加人物图。') : t('Add a source video and a person image first.', '请先添加源视频和人物图。');

const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

function videoSeconds(file) {
    return new Promise(resolve => {
        const url = URL.createObjectURL(file), probe = document.createElement('video');
        const done = value => { clearTimeout(timer); probe.removeAttribute('src'); probe.load(); URL.revokeObjectURL(url); resolve(value); };
        const timer = setTimeout(() => done(null), 5000);
        probe.preload = 'metadata'; probe.muted = true;
        probe.onloadedmetadata = () => done(Number.isFinite(probe.duration) ? probe.duration : null);
        probe.onerror = () => done(null);
        probe.src = url;
    });
}

// mount: the Media section body. onChange runs after any change to the state.
export function createReplacePerson({t, api, media, mount, view, onChange = () => {}}) {
    const stored = () => media.properties?.[KEY];
    const state = () => { media.properties ??= {}; return media.properties[KEY] ??= {on: false, source: null, person: null, keepSound: false}; };
    const save = info => { media.graph?.change?.(); onChange(info); paint(); };
    const toggle = firstFrameRow(t, 'fv-first-frame fv-replace-person');
    toggle.row.firstChild.textContent = t('Replace person', '替换人物');
    toggle.input.setAttribute('aria-label', t('Replace person', '替换人物'));
    toggle.row.title = '';
    const slots = el('div', 'fv-swap-slots'), note = el('div', 'fv-note fv-swap-note'); note.setAttribute('role', 'status');
    const blockedTitle = t('Not available with first and last frames', '首尾帧不能和替换人物同时使用');
    function slot(kind) {
        const box = el('div', 'fv-swap-slot'), title = el('div', 'fv-swap-title', kind === 'video' ? t('Source video', '源视频') : t('Person image', '人物图'));
        const body = el('button', 'fv-swap-drop'); body.type = 'button';
        const input = el('input'); input.type = 'file'; input.hidden = true;
        input.accept = kind === 'video' ? 'video/*,.mp4,.mov,.webm,.mkv,.m4v' : 'image/*,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff';
        body.onclick = () => input.click();
        input.onchange = () => { const [file] = input.files; input.value = ''; if (file) choose(kind, file); };
        for (const name of ['dragover', 'dragenter']) box.addEventListener(name, event => { event.preventDefault(); event.stopPropagation(); });
        box.addEventListener('drop', event => {
            event.preventDefault(); event.stopPropagation();
            const file = Array.from(event.dataTransfer?.files || []).find(f => referenceKind(f.name) === (kind === 'video' ? 'video' : 'image'));
            if (file) choose(kind, file);
        });
        box.append(title, body, input);
        return {box, body, kind};
    }
    const source = slot('video'), person = slot('image');
    const sound = el('label', 'fv-swap-sound', t('Keep source video sound', '保留源视频声音'));
    const soundInput = el('input'); soundInput.type = 'checkbox'; sound.prepend(soundInput);
    soundInput.onchange = () => { state().keepSound = soundInput.checked; save(); };
    source.box.classList.add('fv-swap-source'); person.box.classList.add('fv-swap-person');
    slots.append(source.box, person.box, sound);
    mount.prepend(toggle.row, slots, note);
    let busy = false;
    async function choose(kind, file) {
        if (busy || media.isUploading) return;
        note.textContent = '';
        if (referenceKind(file.name) !== (kind === 'video' ? 'video' : 'image')) {
            note.textContent = kind === 'video' ? t('Choose a video file.', '请选择视频文件。') : t('Choose an image file.', '请选择图片文件。');
            paint(); return;
        }
        if (kind === 'video') {
            const seconds = await videoSeconds(file);
            if (seconds !== null && seconds < MIN_SOURCE_SECONDS) { note.textContent = t('The source video must be at least 1 second long.', '源视频至少需要 1 秒。'); return; }
        }
        busy = true; media.isUploading = true; paint();
        try {
            const result = await uploadMedia(api, file);
            const next = state();
            if (kind === 'video') { next.source = {file: result.file, audio: result.audio === true}; if (!next.source.audio) next.keepSound = false; }
            else next.person = {file: result.file};
            save();
        } catch (error) { note.textContent = error.message; }
        finally { busy = false; media.isUploading = false; paint(); }
    }
    function fill(part, entry, empty) {
        part.body.replaceChildren();
        part.body.classList.toggle('fv-swap-filled', Boolean(entry));
        if (!entry) { part.body.append(el('span', 'fv-swap-plus', '+'), el('span', 'fv-swap-sub', empty)); part.body.title = empty; return; }
        const thumb = part.kind === 'video' ? el('video', 'fv-swap-thumb') : el('img', 'fv-swap-thumb');
        if (part.kind === 'video') { thumb.muted = true; thumb.preload = 'metadata'; thumb.src = view(entry.file) + '#t=0.1'; }
        else { thumb.alt = ''; thumb.src = view(entry.file); }
        part.body.append(thumb, el('span', 'fv-swap-name', entry.file.split('/').pop()));
        part.body.title = entry.file.split('/').pop();
    }
    function paint() {
        const current = stored(), on = current?.on === true, blocked = framesPresent(media);
        toggle.input.checked = on;
        toggle.setDisabled(Boolean(busy) || (blocked && !on));
        toggle.input.title = blocked && !on ? blockedTitle : '';
        toggle.row.classList.toggle('fv-swap-blocked', blocked && !on);
        slots.hidden = !on; note.hidden = !on || !note.textContent;
        mount.classList.toggle('fv-swap-on', on);
        fill(source, current?.source, t('Add video', '添加视频'));
        fill(person, current?.person, t('Add image', '添加图片'));
        const audio = current?.source?.audio === true;
        sound.hidden = !on; soundInput.disabled = busy || !audio; soundInput.checked = audio && current?.keepSound === true;
    }
    toggle.input.onchange = () => {
        if (toggle.input.checked && framesPresent(media)) { toggle.input.checked = false; return; }
        state().on = toggle.input.checked; note.textContent = '';
        save({toggled: true});
    };
    paint();
    const changed = event => { if (String(event.detail) === String(media.id)) paint(); };
    window.addEventListener('freevideo-media', changed);
    return {paint, notify(message) { note.textContent = message || ''; paint(); }, get on() { return swapState(media) !== null; }, element: toggle.row,
        dispose() { window.removeEventListener('freevideo-media', changed); }};
}
