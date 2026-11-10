// A compact LoRA picker for image generation, with the video panel's rows: switch, file,
// strength, trigger words. It keeps its own list in this browser (the image editor has no
// graph node to hold it) and puts trigger words into the prompt the way the video panel does.
import {reconcileLoraTriggers} from './lora_triggers.js';
import {createVerdicts} from './lora_check.js';
import {skinSelect} from './fv_select.js';

export const IMAGE_LORA_LIMIT = 8, IMAGE_LORA_STRENGTH = 2;
const STORE = 'freevideo_image_loras';
const domEl = (tag, label, cls) => { const e = document.createElement(tag); if (label != null) e.textContent = label; if (cls) e.className = cls; return e; };

/** The rows worth sending: switched on, a real strength, a file that is there and fits. */
export function activeLoras(rows, {names = null, verdicts = null} = {}) {
    return rows.filter(row => row.enabled !== false && Number(row.strength) !== 0
        && (!names || names.includes(row.name)) && verdicts?.peek(row.name)?.usable !== false)
        .map(row => ({name: row.name, strength: Number(row.strength), enabled: true}));
}

export function createImageLoras({t, fetchApi, el = domEl, getPrompt = () => '', setPrompt = null, onChange = () => {}, storage = globalThis.localStorage}) {
    let rows = [], records = [], names = null, disposed = false, skipped = 0;
    const words = new Map(), loading = new Map(), drafts = new Map();
    const verdicts = createVerdicts(fetchApi, {kind: 'image'});
    try {
        const saved = JSON.parse(storage?.getItem(STORE) || 'null');
        if (Array.isArray(saved?.rows)) rows = saved.rows.filter(r => r && typeof r.name === 'string').slice(0, IMAGE_LORA_LIMIT)
            .map(r => ({name: r.name, strength: Number.isFinite(Number(r.strength)) ? Number(r.strength) : 1, enabled: r.enabled !== false}));
        // Which trigger words this panel put into a prompt is not carried over: a new description box starts unowned.
    } catch { /* an unreadable saved list starts empty */ }

    const menu = el('details', null, 'fv-ie-menu fv-ie-lora');
    const summary = el('summary', null, 'fv-ie-lora-summary');
    const panel = el('div', null, 'fv-ie-menu-list fv-ie-lora-panel');
    const list = el('div', null, 'fv-ie-lora-list'), actions = el('div', null, 'fv-ie-lora-actions'), note = el('p', '', 'fv-ie-lora-note');
    const add = el('button', t('Add', '添加')); add.type = 'button';
    actions.append(add); panel.append(list, actions, note); menu.append(summary, panel);
    summary.setAttribute('aria-label', 'LoRA');

    const usable = () => activeLoras(rows, {names, verdicts});
    function save() {
        try { storage?.setItem(STORE, JSON.stringify({rows})); } catch { /* private mode */ }
    }
    function drawSummary() {
        const count = usable().length;
        summary.textContent = count ? `LoRA · ${count}` : 'LoRA';
        menu.dataset.active = String(count > 0);
    }
    function syncPrompt() {
        if (!setPrompt) return;
        const next = reconcileLoraTriggers(getPrompt(), records, usable(),
            Object.fromEntries([...words].filter(([, w]) => typeof w === 'string')));
        records = next.records;
        if (next.prompt !== getPrompt()) setPrompt(next.prompt);
    }
    function changed() { syncPrompt(); save(); drawSummary(); onChange(); }

    async function triggerWords(name) {
        if (words.has(name)) return words.get(name);
        if (!loading.has(name)) loading.set(name, (async () => {
            // The first read of a big file hashes it once on the server; keep asking while that finishes.
            for (let attempt = 0; attempt < 10; attempt++) {
                try {
                    const response = await fetchApi('/freevideo/lora/triggers?' + new URLSearchParams({name}),
                        {cache: 'no-store', signal: AbortSignal.timeout(6500)});
                    const result = await response.json();
                    if (response.ok && typeof result?.words === 'string') { words.set(name, result.words); return result.words; }
                    break;
                } catch (error) { if (error?.name !== 'TimeoutError') break; }
            }
            throw new Error(t('Could not read trigger words.', '无法读取触发词。'));
        })().finally(() => loading.delete(name)));
        return loading.get(name);
    }
    async function saveWords(name, value) {
        let result = null;
        try {
            const response = await fetchApi('/freevideo/lora/triggers', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name, words: value.trim()})});
            result = await response.json();
            if (!response.ok || typeof result?.words !== 'string') result = null;
        } catch { /* same sentence below */ }
        if (!result) throw new Error(t('Could not save. Try again.', '没有保存，请再试一次。'));
        words.set(name, result.words);
    }

    function problemText(name, verdict) {
        if (names && !names.includes(name)) return t('The file is no longer in the LoRA folder.', '此文件已不在 LoRA 文件夹中。');
        if (verdict?.usable !== false) return '';
        return verdict.reason === 'unreadable'
            ? t('The file could not be read. It may be incomplete; download it again.', '文件无法读取，可能未下载完整，请重新下载。')
            : t('This LoRA cannot be used for pictures.', '此 LoRA 不适用于图片生成。');
    }

    function render() {
        if (disposed) return;
        list.replaceChildren();
        rows.forEach((row, index) => {
            const wrap = el('div', null, 'fv-ie-lora-row'); wrap.dataset.enabled = String(row.enabled !== false);
            const on = el('input'); on.type = 'checkbox'; on.checked = row.enabled !== false;
            on.setAttribute('aria-label', t('Enable LoRA', '启用 LoRA'));
            const select = el('select'); select.setAttribute('aria-label', t('LoRA model', 'LoRA 模型'));
            for (const name of [...new Set([...(names || []), row.name])]) { const option = el('option', name); option.value = name; select.append(option); }
            select.value = row.name; select.title = row.name;
            const strength = el('input'); strength.type = 'number'; strength.step = '.05';
            strength.min = String(-IMAGE_LORA_STRENGTH); strength.max = String(IMAGE_LORA_STRENGTH); strength.value = row.strength;
            strength.setAttribute('aria-label', t('LoRA strength', 'LoRA 强度'));
            const remove = el('button', '×'); remove.type = 'button'; remove.setAttribute('aria-label', t('Remove LoRA', '移除 LoRA'));
            const trigger = el('input', null, 'fv-ie-lora-trigger'); trigger.type = 'text'; trigger.maxLength = 300;
            trigger.placeholder = t('Trigger words', '触发词'); trigger.setAttribute('aria-label', trigger.placeholder);
            trigger.value = drafts.get(row.name) ?? words.get(row.name) ?? '';
            const problem = el('p', '', 'fv-ie-lora-problem'); problem.hidden = true;
            const show = () => {
                const text = problemText(row.name, verdicts.peek(row.name));
                problem.textContent = text; problem.hidden = !text;
                wrap.dataset.unusable = String(!!text);
                on.disabled = !!text; strength.disabled = trigger.disabled = !!text || row.enabled === false;
            };
            on.onchange = () => { row.enabled = on.checked; wrap.dataset.enabled = String(on.checked); show(); changed(); };
            select.onchange = () => {
                row.name = select.value; select.title = row.name; trigger.value = words.get(row.name) ?? '';
                if (verdicts.peek(row.name)?.usable === false) row.enabled = true;
                on.checked = row.enabled !== false; check(); changed();
            };
            strength.onchange = () => {
                const value = Number(strength.value);
                if (strength.value === '' || !(Math.abs(value) <= IMAGE_LORA_STRENGTH)) {
                    strength.setCustomValidity(t(`Enter a value from -${IMAGE_LORA_STRENGTH} to ${IMAGE_LORA_STRENGTH}.`, `请输入 -${IMAGE_LORA_STRENGTH} 到 ${IMAGE_LORA_STRENGTH} 之间的数。`));
                    strength.reportValidity(); return;
                }
                strength.setCustomValidity(''); row.strength = value; changed();
            };
            remove.onclick = () => { rows.splice(index, 1); render(); changed(); };
            trigger.onchange = async () => {
                const name = row.name, draft = trigger.value;
                drafts.set(name, draft);
                try { await saveWords(name, draft); drafts.delete(name); note.textContent = ''; syncPrompt(); save(); onChange(); }
                catch (error) { note.textContent = error.message; }
            };
            const check = () => verdicts.get(row.name).then(() => { if (!disposed && wrap.isConnected && select.value === row.name) { show(); drawSummary(); syncPrompt(); onChange(); } });
            wrap.append(on, skinSelect(select), strength, remove, trigger, problem);
            list.append(wrap); show();
            if (!verdicts.peek(row.name)) check();
            triggerWords(row.name).then(() => {
                if (!disposed && trigger.isConnected && !drafts.has(row.name)) trigger.value = words.get(row.name) ?? '';
                syncPrompt(); save(); drawSummary(); onChange();
            }, error => { note.textContent = error.message; });
        });
        add.disabled = rows.length >= IMAGE_LORA_LIMIT;
        if (!note.textContent || !rows.some(r => drafts.has(r.name))) note.textContent = skipped
            ? t(`${skipped} other LoRA file${skipped === 1 ? '' : 's'} in the folder ${skipped === 1 ? 'is' : 'are'} not in .safetensors format and not listed.`, `文件夹中另有 ${skipped} 个 LoRA 文件不是 .safetensors 格式，未列出。`) : '';
        drawSummary();
    }

    async function refresh() {
        try {
            const response = await fetchApi('/models/loras', {cache: 'no-store'});
            if (!response.ok) throw new Error(response.statusText);
            const listed = (await response.json()).filter(n => typeof n === 'string');
            skipped = listed.filter(n => /\.(ckpt|pt|pth|bin|sft)$/i.test(n)).length;
            names = listed.filter(n => n.toLowerCase().endsWith('.safetensors'));
            verdicts.clear(); render(); changed();
        } catch (error) {
            if (!disposed) note.textContent = t('Could not read the LoRA folder.', '无法读取 LoRA 文件夹。');
        }
    }
    add.onclick = async () => {
        await refresh();
        if (rows.length >= IMAGE_LORA_LIMIT) { note.textContent = t(`Use at most ${IMAGE_LORA_LIMIT} LoRAs`, `最多使用 ${IMAGE_LORA_LIMIT} 个 LoRA`); return; }
        if (!names?.length) { note.textContent = t('Put LoRA files (.safetensors) in the models/loras folder, then choose "Add".', '把 LoRA 文件（.safetensors）放进 models/loras 文件夹，再点“添加”。'); return; }
        rows.push({name: names.find(n => !rows.some(r => r.name === n)) ?? names[0], strength: 1, enabled: true});
        render(); changed();
    };
    menu.addEventListener('toggle', () => { if (menu.open) refresh(); });
    render();
    return {
        element: menu,
        /** The LoRAs to send with the request; empty when none is switched on. */
        value: usable,
        count: () => usable().length,
        refresh,
        /** After the prompt box is cleared or replaced, trigger words are put back for the rows still on. */
        reconcile: syncPrompt,
        dispose() { disposed = true; },
    };
}
