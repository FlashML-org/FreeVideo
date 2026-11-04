// Recording from the microphone onto a sound track. A small bar above the tracks holds the microphone choice, the level,
// and the start/stop button; the take is saved as a WAV and placed like any imported sound, never over what is already there.
import {registerMenuItem} from './editor_menu.js';
import {COUNTDOWN_SECONDS, elapsedText, deviceName, recordable, recordRange, landingTrack, replacesTake, secondsOf, takeName, level, mixdown, encodeWav} from './editor_record_model.js';
const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node; };
let styled = false;
function styles() {
    if (styled) return; styled = true;
    const link = el('link'); link.rel = 'stylesheet'; link.href = new URL('./editor_record.css', import.meta.url).href; document.head.append(link);
}
const MIC_PATH = 'M8 1.5a2.5 2.5 0 0 0-2.5 2.5v4a2.5 2.5 0 0 0 5 0V4A2.5 2.5 0 0 0 8 1.5M3.5 7.5a4.5 4.5 0 0 0 9 0M8 12v2.5M5.5 14.5h5';

export function createEditorRecord({state, player, library, t, error = () => {}, notify = () => {}, bar: host, button: toolbarButton}) {
    styles();
    const bar = el('div', 'fv-ed-record'); bar.hidden = true; bar.setAttribute('role', 'group'); bar.setAttribute('aria-label', t('Record', '录音'));
    const devices = el('select', 'fv-ed-record-device'); devices.setAttribute('aria-label', t('Microphone', '麦克风')); devices.hidden = true;
    const meter = el('div', 'fv-ed-record-meter'), fill = el('div', 'fv-ed-record-level'); meter.append(fill); meter.setAttribute('aria-hidden', 'true');
    const main = el('button', 'fv-ed-record-main', t('Start recording', '开始录音')); main.type = 'button';
    const note = el('span', 'fv-ed-record-note'); note.setAttribute('role', 'status');
    const close = el('button', 'fv-ed-record-close', '×'); close.type = 'button'; close.setAttribute('aria-label', t('Close', '关闭'));
    const side = el('div', 'fv-ed-record-side'), center = el('div', 'fv-ed-record-center');
    side.append(devices); center.append(main, meter, note); bar.append(side, center, close);

    let phase = 'idle', denied = false, stream = null, recorder = null, chunks = [], context = null, frame = null, timers = [], disposed = false, session = 0, ticker = null, listening = null, startedAt = 0;
    const clear = () => { timers.forEach(clearTimeout); timers = []; clearInterval(ticker); ticker = null; };
    // The level moves only while the bar is open: the microphone is listened to from opening until closing, and the take reuses that stream.
    const release = () => {
        cancelAnimationFrame(frame); frame = null; listening = null;
        stream?.getTracks().forEach(track => track.stop()); stream = null; context?.close().catch(() => {}); context = null; fill.style.transform = 'scaleX(0)';
    };
    // One request at a time: a quick second choice of microphone waits for the first, then replaces its stream.
    let queue = Promise.resolve();
    const listen = () => queue = queue.then(listening_);
    async function listening_() {
        if (disposed || bar.hidden) return false;
        if (stream && listening === devices.value) return true;
        release();
        const mine = session;
        try {
            const next = await media().getUserMedia({audio: devices.value ? {deviceId: {exact: devices.value}} : true});
            if (mine !== session || disposed || bar.hidden) { next.getTracks().forEach(track => track.stop()); return false; }
            stream = next; listening = devices.value; context = new AudioContext();
            const analyser = context.createAnalyser(); analyser.fftSize = 1024;
            context.createMediaStreamSource(stream).connect(analyser);
            const block = new Float32Array(analyser.fftSize);
            const loop = () => { analyser.getFloatTimeDomainData(block); fill.style.transform = `scaleX(${level(block)})`; frame = requestAnimationFrame(loop); };
            loop(); return true;
        } catch { return false; }
    }
    function paint() {
        main.textContent = phase === 'idle' ? t('Start recording', '开始录音') : t('Stop', '停止');
        main.disabled = denied && phase === 'idle'; meter.hidden = main.disabled; devices.disabled = phase !== 'idle';
        bar.dataset.phase = phase;
        toolbarButton?.setAttribute('aria-pressed', String(!bar.hidden));
    }
    const say = text => { note.textContent = text; };
    const denial = () => { denied = true; say(t('Microphone access was not granted.', '未获得麦克风权限。')); paint(); };
    const media = () => globalThis.navigator?.mediaDevices;

    // Opening the bar asks for the microphone once, so the choice of devices can carry their names.
    async function probe() {
        const mine = ++session;
        try {
            if (!media()?.getUserMedia) throw new Error('unavailable');
            if (!await listen()) throw new Error('denied');
            if (mine !== session || disposed) return;
            denied = false; say('');
            const all = (await media().enumerateDevices()).filter(device => device.kind === 'audioinput');
            // Chrome lists the default and communications choices again under the real device's name: they are left out when a real one exists.
            const real = all.filter(device => !['default', 'communications'].includes(device.deviceId)), inputs = real.length ? real : all.slice(0, 1);
            devices.replaceChildren(...inputs.map((device, index) => { const option = el('option', null, deviceName(device.label, index + 1, t)); option.value = device.deviceId; return option; }));
            devices.hidden = inputs.length < 2;
            // The stream was opened on the system default: show the real device behind it, or open the one shown when none is known.
            const behind = all.find(device => device.deviceId === 'default' && device.groupId);
            const match = behind && inputs.find(device => device.groupId === behind.groupId);
            if (match) devices.value = match.deviceId;
            if (stream) listening = match || inputs.length < 2 ? devices.value : null;
            if (stream && listening === null) await listen();
            paint();
        } catch { if (mine === session && !disposed) denial(); }
    }
    function open() {
        if (disposed || !bar.hidden) return;
        bar.hidden = false; denied = false; say(''); paint(); probe();
    }
    function hide() {
        if (phase !== 'idle') abort();
        session++; bar.hidden = true; release(); paint();
    }
    function abort() {
        clear(); if (recorder && recorder.state !== 'inactive') { recorder.ondataavailable = recorder.onstop = null; try { recorder.stop(); } catch { /* already stopped */ } }
        recorder = null; chunks = []; player.silenceOriginal?.(false); player.pause?.(); phase = 'idle'; say(''); paint();
    }

    async function begin() {
        if (phase !== 'idle' || denied) return;
        const selection = state.selection, range = recordRange(selection, state.playhead);
        if (!landingTrack(state.record, selection, range.start, range.frames ?? 1)) {
            say(t('The recording track is locked.', '录音轨已锁定。')); return;
        }
        const mine = ++session;
        phase = 'counting'; paint();
        if (!await listen()) { if (mine === session) { phase = 'idle'; denial(); } return; }
        if (mine !== session || disposed) return;
        // The count runs before the take, so the speaker has a moment to get ready.
        let left = COUNTDOWN_SECONDS;
        const tick = () => {
            if (mine !== session) return;
            if (left > 0) { say(String(left--)); timers.push(setTimeout(tick, 1000)); } else go(range, mine, selection);
        };
        tick();
    }
    function go(range, mine, selection) {
        phase = 'recording'; paint(); startedAt = performance.now(); say(elapsedText(0));
        ticker = setInterval(() => say(elapsedText((performance.now() - startedAt) / 1000)), 250);
        recorder = new MediaRecorder(stream); chunks = [];
        recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
        recorder.onstop = () => finish(range, mine, selection);
        state.seek(range.start); player.silenceOriginal?.(true);
        recorder.start(); player.play?.();
        if (range.frames) timers.push(setTimeout(stop, Math.ceil(secondsOf(range.frames) * 1000)));
    }
    function stop() {
        if (phase === 'counting') { abort(); return; }
        if (phase !== 'recording') return;
        clear(); phase = 'saving'; paint(); say(t('Saving…', '正在保存…'));
        player.pause?.(); player.silenceOriginal?.(false);
        if (recorder.state !== 'inactive') recorder.stop();
    }
    async function finish(range, mine, selection) {
        const blob = new Blob(chunks, {type: recorder?.mimeType || 'audio/webm'}); chunks = []; recorder = null;
        let file = null, frames = 0;
        try {
            const decoder = new AudioContext(), decoded = await decoder.decodeAudioData(await blob.arrayBuffer()); decoder.close().catch(() => {});
            const limit = range.frames ? Math.round(secondsOf(range.frames) * decoded.sampleRate) : Infinity;
            const samples = mixdown(Array.from({length: decoded.numberOfChannels}, (_, index) => decoded.getChannelData(index)), limit);
            frames = Math.min(range.frames ?? Infinity, Math.ceil(samples.length / decoded.sampleRate * 24));
            if (samples.length) file = new File([encodeWav(samples, decoded.sampleRate)], takeName(new Date(), t), {type: 'audio/wav'});
        } catch (value) { error(value); }
        if (disposed || mine !== session) return;
        phase = 'idle'; say(''); paint();
        if (!file) { notify(t('Nothing was recorded.', '没有录到声音。')); return; }
        bar.hidden = true; release(); paint();
        const before = new Set(state.record.tracks.flatMap(item => item.clips.map(clip => clip.id)));
        const track = landingTrack(state.record, selection, range.start, frames) ?? undefined, replace = track === 'a3' && replacesTake(state.record, range.start, frames);
        await library.importFiles([file], {at: range.start, track, replace: track === 'a3'});
        land(before);
        if (replace) notify(t('Replaced the earlier recording in this range.', '已替换这一段原有的录音。'));
    }
    // The clip just placed settles in with a short highlight, so the eye follows the take from the bar to the track.
    function land(before) {
        const until = performance.now() + 15000, fresh = () => state.record.tracks.flatMap(item => item.clips).filter(clip => !before.has(clip.id)).map(clip => clip.id);
        let shown = 0;
        // The timeline redraws on its own schedule, so the mark is put on each frame until the highlight has run.
        const mark = () => {
            const nodes = fresh().flatMap(id => [...document.querySelectorAll(`.fv-ed-clip[data-clip="${CSS.escape(id)}"]`)]);
            if (nodes.length && !shown) shown = performance.now();
            for (const node of nodes) node.dataset.landed = 'true';
            if (performance.now() < (shown ? shown + 1400 : until)) requestAnimationFrame(mark);
            else for (const node of document.querySelectorAll('.fv-ed-clip[data-landed]')) delete node.dataset.landed;
        };
        requestAnimationFrame(mark);
    }
    // R starts and stops while the bar is open; Esc cancels a take in progress, then closes the bar.
    const typing = target => target?.closest?.('input, textarea, [contenteditable=true]');
    // Called by the editor's key router; true when the key was taken.
    function keys(event) {
        if (event.type !== 'keydown' || bar.hidden || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || typing(event.target)) return false;
        const key = event.key.toLowerCase();
        if (key === 'escape') {
            event.preventDefault(); if (phase !== 'idle') abort(); else hide(); return true;
        }
        if (key === 'r' && !event.target?.closest?.('select') && !event.repeat) { event.preventDefault(); main.onclick(); return true; }
        return false;
    }
    devices.onchange = () => { if (!bar.hidden && phase === 'idle') listen(); };
    main.onclick = () => phase === 'idle' ? begin() : stop();
    close.onclick = hide;
    if (toolbarButton) toolbarButton.onclick = () => bar.hidden ? open() : hide();

    // The menu entry sits on the sound tracks, where an empty spot or a stretch chosen can take a recording.
    const unregister = registerMenuItem({where: 'empty', group: 'sound', label: context => context.t('Record', '录音'),
        visible: context => !context.track || ['a1', 'a2'].includes(context.track), run: () => open()});
    const unsubscribe = state.subscribe(event => { if (event.type === 'selection' && toolbarButton) toolbarButton.hidden = !recordable(state.selection); });
    if (toolbarButton) toolbarButton.hidden = !recordable(state.selection);
    paint();
    return {element: bar, open, hide, keyboard: keys, get phase() { return phase; }, dispose() { disposed = true; unregister(); unsubscribe(); abort(); release(); bar.remove(); }};
}
export const RECORD_ICON = MIC_PATH;
