// The LoRA choice: our own menu (fv_select.js menuSelect) instead of the system list, which can be hundreds of files long.
// Files are grouped under their folder, the recently used ones sit on top, and a filter box narrows the list.
import {menuSelect} from './fv_select.js';

const baseOf = name => name.slice(Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\')) + 1).replace(/\.safetensors$/i, '');
const folderOf = name => { const at = Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\')); return at > 0 ? name.slice(0, at) : ''; };

export function loraMenu(select, t, pinned = []) {
    const recent = new Set(pinned);
    for (const option of select.children) {
        option.dataset.group = recent.has(option.value) ? 'recent' : folderOf(option.value);
        // Under a folder heading only the file name is written; the recent list mixes folders and keeps them.
        option.dataset.label = option.dataset.group === 'recent' ? option.value.replace(/\.safetensors$/i, '') : baseOf(option.value);
    }
    return menuSelect(select, {
        groups: group => group === 'recent' ? t('Recent', '最近使用') : group,
        search: {placeholder: t('Type to filter', '输入文字筛选'), empty: t('No LoRA matches', '没有匹配的 LoRA')},
    });
}
