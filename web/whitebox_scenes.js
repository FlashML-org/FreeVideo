// The ready-made white-model scenes: one is started with the page's chat session, and the Agent's backend
// renders it through this page's whitebox worker and then puts the confirm card into that conversation.
import { agentGet, agentPost } from './agent_session.js';

export const SCENE_TEXT = Object.freeze({
    icon: 'M4 5h7v6H4zM13 5h7v6h-7zM4 13h7v6H4zM13 13h7v6h-7z',
    label: ['Ready-made', '现成场景'], name: ['Start from a ready-made white-model scene', '用现成的白模场景开始'],
    preparing: ['Preparing the white model…', '正在准备白模…'], failed: ['The white model could not be made: ', '白模没有做成：'],
    rendering: ['Rendering the white model…', '正在渲染白模…'], retry: ['. Try again.', '。请重试。'],
    noFit: ['no ready-made scene fits this sentence. Try again.', '没有适合这句话的现成场景。请重试。'],
    describe: ['Describe the shot…', '描述一个镜头…'],
    bare: ['The white model could not be made. Try again.', '白模没有做成。请重试。'],
    noPage: ['The white model could not be made: the renderer is not running in this page. Reload the page and try again.', '白模没有做成：这个页面里的渲染没有运行。请刷新页面后重试。'],
    notStarted: ['The white model could not be made: rendering did not start within 3 minutes. Try again.', '白模没有做成：三分钟内渲染没有开始。请重试。'],
    timeout: ['The white model could not be made: rendering did not finish within 3 minutes. Try again.', '白模没有做成：渲染三分钟内没有完成。请重试。'],
});

export async function sceneList(api) {
    const response = await agentGet(api, '/freevideo/agent/whitebox/presets');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const rows = (await response.json())?.presets;
    return Array.isArray(rows) ? rows.filter(row => typeof row?.id === 'string' && typeof row.name === 'string') : [];
}

export async function startScene(api, preset, session) {
    const response = await agentPost(api, '/freevideo/agent/whitebox/try', {preset, ...(session ? {session} : {})});
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(value?.error?.message || value?.error?.code || `HTTP ${response.status}`);
    return value?.id || null;
}

// The built-in AI picks a scene for the shot: the scene's id, or false when no ready-made scene fits.
export async function pickScene(api, shot, session) {
    const response = await agentPost(api, '/freevideo/agent/whitebox/try', {shot, ...(session ? {session} : {})});
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(value?.error?.message || value?.error?.code || `HTTP ${response.status}`);
    return value?.id || false;
}

// Resolves with the scene's last state: 'ready' (the card follows), 'failed' (with error), or 'timeout' (with the
// last state seen: 'waiting' nobody claimed it, 'rendering' it did not finish, page_open false no page was asking).
// onState hears every state seen, so the page can say what is happening.
export async function watchScene(api, id, {every = 3000, limit = 180000, stopped = () => false, onState = () => {}, wait = ms => new Promise(done => setTimeout(done, ms))} = {}) {
    let last = {};
    for (let waited = 0; waited < limit && !stopped(); waited += every) {
        try {
            const response = await agentGet(api, `/freevideo/agent/whitebox/${encodeURIComponent(id)}`);
            if (response.ok) {
                const value = await response.json();
                if (value?.status === 'ready' || value?.status === 'failed') return value;
                last = value || {}; onState(last);
            }
        } catch { /* Asked again on the next turn. */ }
        await wait(every);
    }
    return {status: 'timeout', last: last.status || '', page_open: last.page_open !== false};
}
