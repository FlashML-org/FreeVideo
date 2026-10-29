// "Generate with AI" page of the new-character dialog: one description becomes five pictures of the
// same person (front, three-quarter, side, back, face). Each picture shows as it is finished; the
// dialog's Save waits for all five. Pictures are made one by one through the page's image client
// and stay in the input folder; the dialog puts them on the card.
import {createImageGenClient, imageGenProgress, imagePackUnavailableMessage, renderImageFailure,
    IMAGE_GEN_POLL_MS, IMAGE_GEN_INFO_POLL_MS} from './image_generation.js';

export {imageGenerationSupported as castGenerateSupported} from './image_generation.js';

export const CAST_VIEWS = Object.freeze(['front', 'three_quarter', 'side', 'back', 'face']);
const VIEW_TITLES = Object.freeze({
    front: ['Front', '正面'], three_quarter: ['Three-quarter', '四分之三'], side: ['Side', '侧面'],
    back: ['Back', '背面'], face: ['Face', '脸部'],
});

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
const failureOf = error => ({error: error?.code || 'generation_failed', report_id: error?.report_id || null,
    stage: error?.stage || '', fitted: error?.fitted || null, memory: error?.memory || ''});
const randomSeed = () => crypto.getRandomValues(new Uint32Array(1))[0] >>> 1;

// The five pictures of one description. No DOM: the page below draws its state.
export function createCastGenerator({client, onChange = () => {}, schedule = setTimeout, unschedule = clearTimeout}) {
    const fresh = () => CAST_VIEWS.map(view => ({view, file: '', fraction: 0}));
    let state = {pictures: fresh(), running: false, current: -1, error: '', report_id: null, waiting: '', description: ''};
    let epoch = 0, timer = null, job = null, plan = null, seed = 0, disposed = false;
    const emit = patch => { state = {...state, ...patch}; if (!disposed) onChange(state); };
    const setPicture = (index, patch) => emit({pictures: state.pictures.map((row, i) => i === index ? {...row, ...patch} : row)});
    const cancelJob = id => Promise.resolve().then(() => client.cancel(id)).catch(() => {});
    const done = () => state.pictures.every(row => row.file);

    async function runOne(index, alive) {
        const view = plan[index], front = state.pictures[0].file;
        const row = await client.generate({prompt: view.prompt, width: view.width, height: view.height, seed,
            images: view.from_front ? [front] : [], ...(view.view === 'back' ? {cast_view: 'back'} : {})});
        if (!row.job) throw Object.assign(new Error(row.error || 'generation_failed'), {code: row.error, report_id: row.report_id, memory: row.memory});
        if (!alive()) { await cancelJob(row.job); return null; }
        job = row.job;
        for (;;) {
            await new Promise(resolve => { timer = schedule(() => { timer = null; resolve(); }, IMAGE_GEN_POLL_MS); });
            if (!alive()) return null;
            const status = await client.status(job);
            if (!alive()) return null;
            if (status.state === 'error') {
                throw Object.assign(new Error(status.error), {code: status.error, report_id: status.report_id,
                    stage: status.stage, fitted: status.fitted, memory: status.memory});
            }
            if (status.state === 'done') {
                if (!status.file) throw new Error('generation_failed');
                return status.file;
            }
            setPicture(index, {fraction: Math.max(state.pictures[index].fraction,
                imageGenProgress(status.stage, status.fraction, view.from_front)), stage: status.stage || ''});
            emit({waiting: status.waiting || ''});
        }
    }

    async function start(description) {
        description = String(description || '').trim();
        if (state.running || disposed || !description) return;
        // After a failure the same description goes on from the picture that failed.
        const resume = !!state.error && description === state.description && plan && !done();
        const current = ++epoch;
        const alive = () => current === epoch && !disposed;
        if (!resume) { seed = randomSeed(); plan = null; }
        emit({running: true, error: '', report_id: null, waiting: '', description,
            ...(resume ? {} : {pictures: fresh(), current: 0})});
        try {
            if (!plan) {
                plan = await client.castViews(description);
                if (!alive()) return;
            }
            for (let index = 0; index < plan.length; index++) {
                if (state.pictures[index].file) continue;
                emit({current: index});
                const file = await runOne(index, alive);
                if (file === null || !alive()) return;
                job = null;
                setPicture(index, {file, fraction: 1});
            }
            emit({running: false, current: -1, waiting: ''});
        } catch (error) {
            if (!alive()) return;
            const id = job; job = null;
            if (id) void cancelJob(id);
            // A failure keeps the pictures already made; the next Generate goes on from the failed one.
            emit({running: false, current: -1, waiting: '', ...failureOf(error),
                pictures: state.pictures.map(row => row.file ? row : {...row, fraction: 0})});
        }
    }

    function stop() {
        const id = job; job = null;
        ++epoch; unschedule(timer); timer = null; plan = null;
        // A stopped run is dropped whole: the next Generate starts again from the description.
        emit({running: false, current: -1, waiting: '', error: '', report_id: null, pictures: fresh()});
        return id ? cancelJob(id) : Promise.resolve();
    }
    // The description changed: pictures made from the old one no longer belong to it.
    function reset() {
        if (state.running || !(state.pictures.some(row => row.file) || state.error)) return;
        plan = null;
        emit({pictures: fresh(), error: '', report_id: null, current: -1});
    }
    return {start, stop, reset, state: () => state, done,
        dispose() { disposed = true; return stop(); }};
}

export function mountCastGenerate({container, api, text, t = text, viewURL, onState = () => {}, fetchApi, client}) {
    const tr = t || ((en) => en);
    const fetcher = fetchApi || (api ? api.fetchApi.bind(api) : globalThis.fetch.bind(globalThis));
    client = client || createImageGenClient(fetcher);
    const url = viewURL || (file => {
        const i = file.lastIndexOf('/');
        const query = new URLSearchParams({filename: file.slice(i + 1), subfolder: file.slice(0, Math.max(0, i)), type: 'input'});
        return (api?.apiURL ? api.apiURL('/view?' + query) : '/view?' + query);
    });
    const cssURL = new URL('./cast_generate.css', import.meta.url).href;
    if (!document.querySelector(`link[href="${cssURL}"]`)) {
        const css = el('link'); css.rel = 'stylesheet'; css.href = cssURL; document.head.append(css);
    }

    const root = el('div', 'fv-cg');
    const body = el('div', 'fv-cg-body');
    const side = el('div', 'fv-cg-side');
    const foot = el('div', 'fv-cg-foot');
    const field = el('textarea', 'fv-cg-text');
    field.placeholder = tr('Describe the character', '描述角色');
    field.setAttribute('aria-label', field.placeholder);
    field.maxLength = 1000;
    const note = el('div', 'fv-cg-note'); note.setAttribute('role', 'status');
    const action = el('button', 'fv-cg-action'); action.type = 'button';
    foot.append(note, action);
    side.append(field, foot);
    const grid = el('div', 'fv-cg-views');
    const cells = CAST_VIEWS.map(view => {
        const cell = el('div', 'fv-cg-view');
        const title = tr(...VIEW_TITLES[view]);
        cell.title = title; cell.setAttribute('role', 'img');
        const image = el('img'); image.alt = ''; image.draggable = false;
        const percent = el('span', 'fv-cg-percent');
        const meter = el('i', 'fv-cg-meter'); const fill = el('b'); meter.append(fill);
        cell.append(image, percent, meter);
        grid.append(cell);
        return {cell, image, percent, fill, title};
    });
    body.append(side, grid);
    root.append(body);
    container.append(root);

    let info = null, infoTimer = null, disposed = false, failureKey = null, attention = null;
    const generator = createCastGenerator({client, onChange: render});
    let last = '';

    function missing() { return info && !info.installed && !info.ready; }
    function render(state = generator.state()) {
        const running = state.running;
        const isMissing = missing();
        root.dataset.state = isMissing ? 'missing' : running ? 'running' : generator.done() ? 'done' : state.error ? 'failed' : 'idle';
        field.readOnly = running;
        action.hidden = !!isMissing;
        // One accent button at a time: Generate until the five pictures exist, then Regenerate is neutral.
        const finishedAll = generator.done() && !running;
        action.textContent = running ? tr('Stop', '停止') : finishedAll ? tr('Regenerate', '重新生成') : tr('Generate', '生成');
        action.dataset.primary = String(!running && !finishedAll);
        const finished = state.pictures.filter(row => row.file).length;
        const currentFraction = running && state.current >= 0 ? state.pictures[state.current]?.fraction || 0 : 0;
        const progress = generator.done() ? 1 : (finished + currentFraction) / CAST_VIEWS.length;
        state.pictures.forEach((row, i) => {
            const cell = cells[i];
            const active = running && i === state.current && !row.file;
            const src = row.file ? url(row.file) : '';
            if (cell.image.getAttribute('src') !== src) src ? cell.image.src = src : cell.image.removeAttribute('src');
            cell.cell.dataset.state = row.file ? 'done' : active ? 'running' : 'empty';
            cell.cell.setAttribute('aria-label', cell.title + (row.file ? '' : active
                ? ' ' + Math.floor(row.fraction * 100) + '%' : ''));
            cell.percent.textContent = active && !state.waiting ? Math.floor(row.fraction * 100) + '%' : '';
            cell.fill.style.width = (active ? row.fraction * 100 : 0) + '%';
        });
        if (state.error) {
            const key = `${state.error}:${state.report_id || ''}:${state.stage || ''}:${state.memory || ''}`;
            if (failureKey !== key) { renderImageFailure(note, {...state, info}, tr, {fetchApi: fetcher}); failureKey = key; }
        } else {
            failureKey = null;
            note.textContent = isMissing ? imagePackUnavailableMessage(info, tr)
                : running && state.waiting ? tr('Added to the queue. It starts when the previous one finishes.',
                    '已加入队列，前面的完成后自动开始。')
                : attention ? attention : '';
        }
        note.hidden = !note.textContent && !note.firstChild;
        note.dataset.failed = String(!!state.error);
        // The last picture lands a moment before the run ends: done means finished, not just five files.
        const complete = generator.done() && !running;
        const summary = JSON.stringify([running, complete, Math.round(progress * 100)]);
        if (summary !== last) {
            last = summary;
            onState({busy: running, progress, done: complete});
        }
    }

    async function checkInfo() {
        try { info = await client.info(); } catch { info = null; }
        if (disposed) return;
        render();
        clearTimeout(infoTimer);
        if (missing()) infoTimer = setTimeout(checkInfo, IMAGE_GEN_INFO_POLL_MS);
    }

    function begin() {
        if (!field.value.trim()) {
            attention = tr('Describe the character first.', '请先描述角色。');
            render(); field.focus();
            return;
        }
        attention = null;
        void generator.start(field.value);
    }
    action.onclick = () => generator.state().running ? void generator.stop() : begin();
    field.addEventListener('input', () => {
        if (attention) { attention = null; render(); }
        if (field.value.trim() !== generator.state().description) generator.reset();
    });
    // Enter in the description box is a new line; Ctrl/Cmd+Enter generates.
    field.addEventListener('keydown', event => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !generator.state().running) { event.preventDefault(); begin(); }
    });

    render();
    void checkInfo();
    return {
        getPictures: () => generator.done() && !generator.state().running ? generator.state().pictures.map(row => ({file: row.file, label: row.view})) : [],
        isBusy: () => generator.state().running,
        cancel: () => generator.stop(),
        destroy() { disposed = true; clearTimeout(infoTimer); void generator.dispose(); root.remove(); },
        element: root,
    };
}
