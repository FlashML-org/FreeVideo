const untitled = name => ['Untitled edit', '未命名剪辑'].includes(name);
const sorted = value => value && typeof value === 'object'
    ? Array.isArray(value) ? value.map(sorted) : Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])])) : value;

export function discardEmptyEdit(record, initial, {busy = false, hasMedia = false} = {}) {
    if (busy || hasMedia || record.keep_empty || !untitled(record.name)
        || record.tracks.some(track => track.clips.length) || record.replacements?.length) return false;
    // Compare settings, track controls and sparse project fields with initial().
    // An untouched empty second sound track does not make an edit differ from a new one.
    const bare = track => ['a2', 'a3'].includes(track.id) && !track.clips.length && Object.keys(track).every(key => ['id', 'kind', 'clips'].includes(key));
    const content = value => {
        const {id, revision, serial, name, ...rest} = value;
        return {...rest, tracks: rest.tracks.filter(track => !bare(track))};
    };
    return JSON.stringify(sorted(content(record))) === JSON.stringify(sorted(content(initial)));
}

export const hiddenEmptyEdit = (row, initial) => row.frames === 0 && !row.keep_empty && untitled(row.name)
    && row.width === initial.width && row.height === initial.height;

export const deleteEditText = (t, name = '') => ({
    item: t('Delete edit', '删除剪辑'),
    confirm: t(`Delete "${name}"? This can't be undone.`, `删除“${name}”？删除后无法恢复。`),
    remove: t('Delete', '删除'), cancel: t('Cancel', '取消'),
    project_in_use: t('This edit is open in another window. Close it there first.', '这个剪辑在另一个窗口中打开，请先在那里关闭。'),
    generation_active: t('This edit is still generating. You can delete it when it finishes.', '正在生成，完成后才能删除。'),
    delete_failed: t('Could not delete this edit. Please try again.', '删除失败，请重试。'),
});
