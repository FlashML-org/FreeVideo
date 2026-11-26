// The name a new edit gets ('Untitled edit' through t(), so in the language of the page), in every interface language. An edit
// with one of these names was never named, whichever language made it: an empty one is still left out and put away after the
// language changes. scripts/i18n_check.py fails a language file whose 'Untitled edit' is not in this list. (Chinese first and
// English last: the text collector reads an English text followed by a Chinese one as a pair to translate.)
export const UNTITLED_EDIT_NAMES = Object.freeze([
    '未命名剪辑', '未命名剪輯', '無題の編集', '제목 없는 편집',
    'Edición sin título', 'Edição sem título', 'Montage sans titre', 'Unbenannt', 'Montaggio senza titolo', 'Senza titolo',
    'Без названия', 'Без назви', 'Adsız kurgu', 'Tanpa judul', 'Bản dựng phim chưa đặt tên', 'Chưa đặt tên',
    'Untitled edit',
]);
const untitled = name => UNTITLED_EDIT_NAMES.includes(name);
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
    confirm: t('Delete "{name}"? This can\'t be undone.', '删除“{name}”？删除后无法恢复。', { name }),
    remove: t('Delete', '删除'), cancel: t('Cancel', '取消'),
    also: residue => {
        // Each kind: how many, then its one and its many as whole phrases.
        const kinds = [
            [residue?.imports?.length, () => t('1 imported file', '1 个导入素材'), count => t('{count} imported files', '{count} 个导入素材', { count })],
            [residue?.music?.length, () => t('1 music track', '1 段配乐'), count => t('{count} music tracks', '{count} 段配乐', { count })],
            [residue?.voice?.length, () => t('1 voiceover', '1 段配音'), count => t('{count} voiceovers', '{count} 段配音', { count })],
            [residue?.stems?.length, () => t('1 stem set', '1 组分轨'), count => t('{count} stem sets', '{count} 组分轨', { count })],
            [residue?.sound?.length, () => t('1 sound effect', '1 段音效'), count => t('{count} sound effects', '{count} 段音效', { count })]];
        const items = kinds.filter(([count]) => count).map(([count, one, many]) => count === 1 ? one() : many(count));
        return items.length ? t('Also deletes media used only by this edit: {items}.', '同时删除只在这个剪辑中使用的 {items}。', { items: items.join(t(', ', '、')) }) : '';
    },
    project_in_use: t('This edit is open in another window. Close it there first.', '这个剪辑在另一个窗口中打开，请先在那里关闭。'),
    generation_active: t('This edit is still generating. You can delete it when it finishes.', '正在生成，完成后才能删除。'),
    delete_failed: t('Could not delete this edit. Please try again.', '删除失败，请重试。'),
});
