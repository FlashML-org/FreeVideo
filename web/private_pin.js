// The FreeVideo files the open workflow uses, told to the engine so that its cleanup keeps them while this page
// has them. Only names of FreeVideo's own files are sent; a tab's newest list replaces its last one.
import {api} from '../../scripts/api.js';
import {app} from '../../scripts/app.js';
import {privacyOffered} from './private_offers.js';

const FILE = /freevideo(?:-image(?:-ref)?|-draft)?\/[^"\\\s]+|freevideo-image-(?:reference|mask)-[0-9a-f-]{36}\.png/g;
const REFRESH_MS = 6 * 3600 * 1000;
let page = '', timer = 0, last = null, started = false, absent = false;

function pageId() {
    if (page) return page;
    try { page = sessionStorage.getItem('freevideo.pin.page') || ''; } catch { /* this tab only */ }
    if (!page) {
        page = crypto.randomUUID().replace(/-/g, '');
        try { sessionStorage.setItem('freevideo.pin.page', page); } catch { /* this tab only */ }
    }
    return page;
}

export function pinnedFiles(graph) {
    let text = '';
    try { text = JSON.stringify(graph?.serialize?.() || {}); } catch { return null; }
    return [...new Set(text.match(FILE) || [])].sort().slice(0, 512);
}

export async function pinNow() {
    // An engine without the call (older than this page) is sent nothing.
    if (absent || !(await privacyOffered())) return;
    const files = pinnedFiles(app.graph);
    if (!files) return;
    const key = files.join('\n');
    if (key === last) return;
    try {
        const response = await api.fetchApi('/freevideo/private/pin', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({page: pageId(), files})});
        if (response.ok) last = key;
        // An engine without it (older than this page): asked no more in this tab.
        else if (response.status === 404 || response.status === 405) absent = true;
    } catch { /* the next change or refresh tries again */ }
}

export function pinSoon() {
    clearTimeout(timer); timer = setTimeout(() => void pinNow(), 1500);
    if (!started) {
        started = true;
        // A list is kept a week; an open tab renews its own.
        setInterval(() => { last = null; void pinNow(); }, REFRESH_MS);
    }
}
