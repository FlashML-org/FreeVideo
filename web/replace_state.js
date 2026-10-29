// Replace person: the saved state and the rules built on it. No imports, so any module (the media list, the prompt
// numbering, the request) can read it without a cycle; the panel itself is in replace_person.js.
export const SWAP_LORA = 'alissonerdx-v1.1.safetensors';
export const SWAP_PREFIX = 'body_swap:';
export const MIN_SOURCE_SECONDS = 22 / 24; // the engine needs 22 frames
export const SWAP_KEY = 'freevideo_replace_person';

export const swapState = media => {
    const state = media?.properties?.[SWAP_KEY];
    return state && typeof state === 'object' && state.on === true ? state : null;
};
export const keepsSound = state => Boolean(state?.keepSound && state.source?.audio === true);

// Person image first (it is Picture 1), then the source video as a guide clip. `slot` keeps the number of each when the
// file in it is replaced.
export function swapRows(state) {
    const rows = [];
    if (state?.person?.file) rows.push({file: state.person.file, role: 'reference', enabled: true, slot: 'person'});
    if (state?.source?.file) rows.push({file: state.source.file, role: 'reference', enabled: true, guide: true, slot: 'source',
        ...(keepsSound(state) ? {keep_source_audio: true} : {})});
    return rows;
}

// What the request carries: the rows without the editor-only slot marker.
export const requestRows = state => swapRows(state).map(({slot, ...row}) => row);

// 'both', 'source', 'person' for what is still missing, null when the two slots are filled.
export function swapMissing(state) {
    const source = Boolean(state?.source?.file), person = Boolean(state?.person?.file);
    return source && person ? null : !source && !person ? 'both' : source ? 'person' : 'source';
}

// The media list a request reads: the two slots while Replace person is on, else the ordinary list.
export function effectiveRows(media, serialized) {
    const state = swapState(media);
    return state ? swapRows(state) : JSON.parse(serialized?.value || '[]');
}

export const withPrefix = text => {
    const body = String(text ?? '').trim();
    return body ? `${SWAP_PREFIX} ${body}` : SWAP_PREFIX;
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
