// Videos made by one task (a preview and its upscale, a continuation, one agent job, the same prompt on one day)
// share the `task` the library API gives them. A list shows the newest as the cover; the others fold under it
// until the cover is pressed again. Both lists (Recent videos and Creations) fold with this one rule.

// Marks the cards of `order` (video ids, newest first) for their tasks. `open` is the task now unfolded;
// while picking several creations every card shows.
export function applyTasks({cards, rows, order, open = null, selecting = false, selected = null, t, label}) {
    const groups = new Map();
    for (const video of order) {
        const task = rows.get(video)?.task;
        if (task) groups.set(task, [...(groups.get(task) || []), video]);
    }
    const heads = new Map();
    for (const [task, videos] of groups) if (videos.length > 1) heads.set(videos[0], {task, videos});
    for (const video of order) {
        const card = cards.get(video);
        if (!card) continue;
        const task = rows.get(video)?.task, group = task && groups.get(task);
        const folded = group && group.length > 1;
        const shown = folded && (selecting || open === task);
        const head = heads.get(video);
        card.classList.toggle('fv-task-head', !!head);
        card.classList.toggle('fv-task-open', !!head && shown);
        card.classList.toggle('fv-task-member', !!folded && !head);
        card.hidden = !!folded && !head && !shown;
        if (head && !selecting) {
            card.setAttribute('aria-expanded', String(shown));
            card.setAttribute('aria-label', t(`${group.length} results`, `${group.length} 个结果`));
            // A folded cover stands for every video under it.
            if (!shown) card.setAttribute('aria-pressed', String(group.includes(selected)));
        } else {
            card.removeAttribute('aria-expanded');
            card.setAttribute('aria-label', label(video));
        }
    }
    return heads;
}

// The task to unfold when `video` is pressed: only a cover with others under it unfolds, and pressing it again folds it.
export function toggledTask(rows, order, video, open) {
    const task = rows.get(video)?.task;
    if (!task || order.find(item => rows.get(item)?.task === task) !== video) return open;
    if (order.filter(item => rows.get(item)?.task === task).length < 2) return open;
    return open === task ? null : task;
}
