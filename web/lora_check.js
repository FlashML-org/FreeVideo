// What the LoRA check found, as words for the row; the check itself is /freevideo/lora/check.
const FAMILIES = {wan: 'Wan 2.x', hunyuan: 'HunyuanVideo', ltx: 'LTX-Video', sd: 'Stable Diffusion'};
const size = shape => Array.isArray(shape) ? shape.join('×') : '';

/** Row notice: partial application has no details; unusable files keep their explanation. */
export function describeVerdict(verdict, t) {
    if (!verdict) return null;
    if (verdict.usable !== false) return verdict.usable === true && verdict.precision === 'int8' && verdict.partial > 0
        ? {partial: true, reason: t('Partly applied', '此 LoRA 部分生效')} : null;
    const family = FAMILIES[verdict.family];
    let reason;
    if (verdict.reason === 'unreadable') reason = t('the file could not be read. It may be incomplete; download it again.', '文件无法读取，可能未下载完整，请重新下载');
    else if (verdict.reason === 'int8-unmergeable') reason = t('This LoRA cannot be used', '此 LoRA 无法使用');
    else if (verdict.reason === 'format') reason = t('not a LoRA file FreeVideo can read', '不是 FreeVideo 能读取的 LoRA 文件');
    else if (verdict.reason === 'shape') reason = t('does not match the current model', '和当前模型的结构不匹配');
    else if (verdict.reason === 'acceleration') reason = t('an acceleration LoRA made for the original model. The FreeVideo model is already accelerated and does not need one',
        '这是为原版模型制作的加速 LoRA。FreeVideo 的模型已经加速，无需此类 LoRA');
    else reason = family ? t(`made for ${family}`, `这是给 ${family} 训练的 LoRA`) : t('not made for the FreeVideo model', '不适用于 FreeVideo 的模型');
    const summary = verdict.summary || {};
    const lines = [];
    if (verdict.reason === 'shape') {
        const where = Number.isInteger(verdict.block) ? t(`Layer ${verdict.block}: `, `第 ${verdict.block} 层：`) : '';
        lines.push(where + t(`expected ${size(verdict.expected)}, file has ${size(verdict.actual)}`, `期望 ${size(verdict.expected)}，文件是 ${size(verdict.actual)}`));
    }
    if (verdict.layer) lines.push(t('Layer: ', '层：') + verdict.layer);
    if (verdict.count > 1) lines.push(t(`${verdict.count} layers do not match`, `共 ${verdict.count} 层不匹配`));
    if (Number.isInteger(summary.keys)) lines.push(t(`${summary.keys} tensors`, `共 ${summary.keys} 个张量`));
    for (const [key, shape] of summary.first || []) lines.push(`${key}  ${size(shape)}`);
    for (const [key, value] of Object.entries(summary.base || {})) lines.push(`${key}: ${value}`);
    return {reason, lines};
}

/** The text and header summary for a prefilled issue; never the file or its path. */
export function feedbackDraft(name, verdict, described, t = (en) => en) {
    const base = String(name).split('/').pop();
    const text = ['FreeVideo marked this LoRA as unusable. The verdict is reported as a misjudgment.', `File: ${base}`,
        `Check result: ${verdict.reason || 'unknown'}${verdict.family ? ` (${verdict.family})` : ''}${described ? `, ${described.reason}` : ''}`].join('\n');
    const summary = verdict.summary || {};
    const diagnostics = JSON.stringify({check: {reason: verdict.reason, family: verdict.family, layer: verdict.layer, block: verdict.block,
        expected: verdict.expected, actual: verdict.actual, count: verdict.count},
        header: {keys: summary.keys, first: summary.first, base: summary.base}});
    return {title: (t('[Feedback] LoRA compatibility misjudged: ', '[Feedback] LoRA 兼容判断有误：') + base).slice(0, 120), text, diagnostics};
}

/** Verdicts by name. Refresh also checks the currently installed model; failures say nothing. */
export function createVerdicts(fetchApi, {kind = null} = {}) {
    let known = new Map(), pending = new Map();
    return {
        peek: name => known.get(name),
        clear() { known = new Map(); pending = new Map(); },
        async get(name, {refresh = false} = {}) {
            if (!refresh && known.has(name)) return known.get(name);
            if (!pending.has(name)) {
                const mine = pending, request = (async () => {
                    try {
                        const response = await fetchApi('/freevideo/lora/check?' + new URLSearchParams(kind ? {name, kind} : {name}), {cache: 'no-store'});
                        const verdict = response.ok ? await response.json() : null;
                        if (mine !== pending) return null;
                        if (verdict && typeof verdict.usable === 'boolean') known.set(name, verdict);
                        return verdict;
                    } catch { return null; }
                    finally { mine.delete(name); }
                })();
                pending.set(name, request);
            }
            return pending.get(name);
        },
    };
}
