// Which edit the editor opens on entry. A draft of this tab first, then the edit this browser had open last; a browser
// that never opened one (a fresh profile) opens the most recently saved edit that has something in it, and only when
// there is none a blank edit.
// rows: the saved edits' list ({id, frames, updated_at, ...}); last: this browser's last edit id; draft: this tab's draft key.
export function openingChoice({last = null, draft = null, rows = null} = {}) {
    if (typeof draft === 'string' && draft.startsWith('draft:')) return {kind: 'draft', id: draft};
    if (last) return {kind: 'last', id: last};
    const time = row => { const value = Date.parse(row?.updated_at || ''); return Number.isFinite(value) ? value : 0; };
    const latest = (Array.isArray(rows) ? rows : []).filter(row => row && typeof row.id === 'string' && Number(row.frames) > 0)
        .sort((a, b) => time(b) - time(a))[0];
    return latest ? {kind: 'latest', id: latest.id} : {kind: 'blank', id: null};
}
