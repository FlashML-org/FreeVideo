// Replace person: the saved state and the rules built on it. No imports, so any module (the media list, the prompt
// numbering, the request) can read it without a cycle; the panel itself is in replace_person.js.
export const SWAP_LORA = 'alissonerdx-v1.1.safetensors';
export const SWAP_PREFIX = 'body_swap:';
export const MIN_SOURCE_SECONDS = 22 / 24; // the engine needs 22 frames
export const SWAP_KEY = 'freevideo_replace_person';
export const MAX_PEOPLE = 2; // person images in one request, matched to the people in the video from left to right

export const swapState = media => {
    const state = media?.properties?.[SWAP_KEY];
    return state && typeof state === 'object' && state.on === true ? state : null;
};
export const keepsSound = state => Boolean(state?.keepSound && state.source?.audio === true);

// The person images, in the order they are matched to the people in the video (left to right). A state saved with one
// person image has only `person`; a state with several keeps `people` and mirrors the first into `person`.
export function peopleOf(state) {
    const list = Array.isArray(state?.people) ? state.people : state?.person ? [state.person] : [];
    return list.filter(entry => entry?.file).slice(0, MAX_PEOPLE);
}
// What the state stores for a list of person images: `person` stays the first, so a state with one image reads as before.
export const withPeople = (state, people) => Object.assign(state, {people, person: people[0] || null});
const personSlot = index => index ? `person${index + 1}` : 'person';

// Person images first (they are Picture 1, 2, ...), then the source video as a guide clip. `slot` keeps the number of
// each when the file in it is replaced.
export function swapRows(state) {
    const rows = [];
    peopleOf(state).forEach((entry, index) => rows.push({file: entry.file, role: 'reference', enabled: true, slot: personSlot(index)}));
    if (state?.source?.file) rows.push({file: state.source.file, role: 'reference', enabled: true, guide: true, slot: 'source',
        ...(keepsSound(state) ? {keep_source_audio: true} : {})});
    return rows;
}

// What the request carries: the rows without the editor-only slot marker.
export const requestRows = state => swapRows(state).map(({slot, ...row}) => row);

// 'both', 'source', 'person' for what is still missing, null when the two slots are filled.
export function swapMissing(state) {
    const source = Boolean(state?.source?.file), person = peopleOf(state).length > 0;
    return source && person ? null : !source && !person ? 'both' : source ? 'person' : 'source';
}

// The media list a request reads: the two slots while Replace person is on, else the ordinary list.
export function effectiveRows(media, serialized) {
    const state = swapState(media);
    return state ? swapRows(state) : JSON.parse(serialized?.value || '[]');
}

// With several person images the request says who replaces whom: the pictures are in the order of the people in the
// frame from left to right. A prompt that already names its pictures is left as the user wrote it. The wording is the
// one that kept the order of two people right in every test run; it names the side of the frame for each picture.
const NAMES_PICTURE = /\bPicture\s*\d/i;
const SIDES = ['left', 'right'];
export function peopleSentence(count) {
    return SIDES.slice(0, Math.min(count, SIDES.length)).map((side, index) => `Picture ${index + 1} replaces the person on the ${side} side of the frame.`).join(' ');
}
export const withPrefix = (text, count = 1) => {
    const body = String(text ?? '').trim();
    const sentence = count > 1 && !NAMES_PICTURE.test(body) ? peopleSentence(Math.min(count, MAX_PEOPLE)) : '';
    const rest = [sentence, body].filter(Boolean).join(' ');
    return rest ? `${SWAP_PREFIX} ${rest}` : SWAP_PREFIX;
};

// The replace-person adapter as ComfyUI registers it: the same file may sit in a subfolder of models/loras.
const baseName = name => String(name).split(/[\\/]/).pop().toLowerCase();
export const swapLoraNames = names => (Array.isArray(names) ? names : []).filter(name => typeof name === 'string' && baseName(name) === SWAP_LORA);
const active = row => row.enabled !== false && Number(row.strength) > 0;

// Makes the adapters list carry one working replace-person row. Returns {ok, adapters, record}; record says how to undo
// what was done (a row added, or a row of the user's switched on) and stays as it was when the row is already working.
// ok is false with reason 'missing' (not installed), 'ambiguous' (several installed, none chosen) or 'unreadable'.
export function prepareSwapLora(adapters, names, previous = null) {
    let rows; try { rows = JSON.parse(adapters || '[]'); } catch { return {ok: false, reason: 'unreadable'}; }
    if (!Array.isArray(rows)) return {ok: false, reason: 'unreadable'};
    const found = swapLoraNames(names);
    if (!found.length) return {ok: false, reason: 'missing'};
    let name = found.length === 1 ? found[0] : null;
    if (!name) {
        const used = [...new Set(rows.filter(row => found.includes(row.name) && active(row)).map(row => row.name))];
        const listed = [...new Set(rows.filter(row => found.includes(row.name)).map(row => row.name))];
        const pick = used.length ? used : listed;
        if (pick.length !== 1) return {ok: false, reason: 'ambiguous'};
        name = pick[0];
    }
    const index = rows.findIndex(row => row.name === name), keep = previous?.name === name ? previous : null;
    if (index < 0) return {ok: true, adapters: JSON.stringify([...rows, {name, strength: 1, enabled: true}]), record: keep || {name, added: true}};
    const row = rows[index];
    if (active(row)) return {ok: true, adapters: JSON.stringify(rows), record: keep};
    const record = keep || {name, restore: {...('enabled' in row ? {enabled: row.enabled} : {}), ...('strength' in row ? {strength: row.strength} : {})}};
    rows[index] = {...row, enabled: true, strength: Number(row.strength) > 0 ? row.strength : 1};
    return {ok: true, adapters: JSON.stringify(rows), record};
}

// Undoes prepareSwapLora: a row it added goes, a row it switched on goes back to how the user had it.
export function releaseSwapLora(adapters, record) {
    let rows; try { rows = JSON.parse(adapters || '[]'); } catch { return adapters; }
    if (!Array.isArray(rows) || !record?.name) return adapters;
    if (record.added) rows = rows.filter(row => row.name !== record.name);
    else if (record.restore) rows = rows.map(row => {
        if (row.name !== record.name) return row;
        const {enabled, strength, ...rest} = row;
        return {...rest, ...record.restore};
    });
    return JSON.stringify(rows);
}
