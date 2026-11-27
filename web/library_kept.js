// What Creations says when a delete keeps some creations, and which edit "Open edit" opens.
// t(en, zh, values) is the page's translator; stamp(iso) writes a time the way the creations list writes dates.

const DEFAULT_NAMES = new Set(['Untitled edit', '未命名剪辑']);
const LONGEST = 32;

// The reasons a creation was kept, in the order the summary names them. A creation whose folder is not laid out as
// FreeVideo writes it is kept for the same kind of reason as one whose folder holds other files.
const PARTS = [
    [['editor-in-use'], t => n => t('{n} used in edits', '{n} 个正被剪辑使用', { n })],
    [['continuation-in-use'], t => n => t('{n} used by a continuation that is running or queued', '{n} 个正被接着生成的任务使用', { n })],
    [['in-use'], t => n => t('{n} in use by a second pass or another program', '{n} 个正被二采或其他程序使用', { n })],
    [['failed'], t => n => t('{n} without permission to delete', '{n} 个没有删除权限', { n })],
    [['foreign-files', 'not-freevideo'], t => n => t('{n} in folders that hold other files', '{n} 个所在文件夹里有别的文件', { n })],
    [['unavailable'], t => n => t('{n} whose files no longer exist', '{n} 个文件已不存在', { n })],
    [['unsaved', 'editor-unavailable'], t => n => t('{n} that cannot be deleted right now (try again later)', '{n} 个暂时无法删除（请稍后再试）', { n })],
];

// A name in Chinese stays on one line: its characters and its quotes are joined when shown (the language files keep
// plain words). The time after a name may wrap, so two names in a row do not make one long unbreakable run.
export function holdName(text) {
    return /[\u3400-\u9fff]/.test(text) ? [...text].join('\u2060') : text;
}

function shortName(name) {
    const chars = [...String(name || '')];
    return chars.length > LONGEST ? chars.slice(0, LONGEST).join('') + '…' : chars.join('');
}

// One edit as a sentence names it: quoted; a default name, or a name another edit in the same sentence also has,
// carries its last save so the two can be told apart.
function editName(t, edit, edits, stamp) {
    const name = shortName(edit.name), clash = DEFAULT_NAMES.has(edit.name) || edits.some(other => other !== edit && other.name === edit.name);
    const time = clash && edit.updated_at ? stamp(edit.updated_at) : '', quoted = holdName(t('"{name}"', '“{name}”', { name }));
    return time ? t('{quoted} (edited {time})', '{quoted}（{time} 修改）', { quoted, time }) : quoted;
}

// The sentence for one creation kept because saved edits use it, and the edit "Open edit" opens (the first, newest).
export function editsText(t, edits, stamp) {
    const list = Array.isArray(edits) ? edits.filter(edit => edit && typeof edit.id === 'string') : [];
    if (!list.length) return null;
    const names = list.slice(0, 2).map(edit => editName(t, edit, list, stamp)), open = {edit: list[0].id, clip: list[0].clip || null};
    if (list.length === 1 && list[0].where === 'version') return {open, text: t(
        'This creation is used in an earlier version of a part in the edit {name} (from before it was regenerated), which the timeline does not show, so it was kept. Open the edit, select that part and look under "Version".',
        '这个作品用在剪辑{name}中某一段的较早版本里（重新生成之前的版本），时间线上看不到，已保留。打开剪辑后，选中那一段，在“版本”里可以看到它。', { name: names[0] })};
    if (list.length === 1) return {open, text: t('This creation is used in the edit {name}, so it was kept. Remove it from the edit, then delete it.',
        '这个作品正被剪辑{name}使用，已保留。从剪辑中移除后，再删除。', { name: names[0] })};
    if (list.length === 2) return {open, text: t('This creation is used in the edits {name1} and {name2}, so it was kept. Remove it from those edits, then delete it.',
        '这个作品正被剪辑{name1}和{name2}使用，已保留。从这些剪辑中移除后，再删除。', { name1: names[0], name2: names[1] })};
    return {open, text: t('This creation is used in the edits {name1}, {name2} and {n} more, so it was kept. Remove it from those edits, then delete it.',
        '这个作品正被剪辑{name1}{name2}和另外 {n} 个剪辑使用，已保留。从这些剪辑中移除后，再删除。', { name1: names[0], name2: names[1], n: list.length - 2 })};
}

// The summary when several creations were asked to go: how many stayed and why (the status line already says how many
// were deleted, with Undo). In selecting, the kept ones stay selected and the line says so. Reasons with no creation are
// left out.
export function keptSummary(t, kept, {selecting = true} = {}) {
    const counts = PARTS.map(([reasons, word]) => [kept.filter(row => reasons.includes(row.reason)).length, word(t)]);
    const known = new Set(PARTS.flatMap(([reasons]) => reasons)), other = kept.filter(row => !known.has(row.reason)).length;
    if (other) counts[counts.length - 1][0] += other;
    const parts = counts.filter(([n]) => n).map(([n, word]) => word(n)).join(t('; ', '；'));
    if (!selecting) return t('Kept {kept}: {parts}.', '保留了 {kept} 个：{parts}。', { kept: kept.length, parts });
    return kept.length === 1 ? t('Kept {kept}: {parts}. The kept creation stays selected.', '保留了 {kept} 个：{parts}。保留的作品仍为选中状态。', { kept: kept.length, parts })
        : t('Kept {kept}: {parts}. The kept creations stay selected.', '保留了 {kept} 个：{parts}。保留的作品仍为选中状态。', { kept: kept.length, parts });
}
