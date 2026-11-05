// The "Report a problem" button that sits beside a failure message: one component for every place that
// shows one. A click opens the GitHub feedback form with the error sentence already in it; the report
// and typed text never travel (the message goes through the same redaction as the failure card's report).
import {createErrorReport} from './error_panel.js';
import {focusReportTip, reportIssue, showReportTip} from './report_issue.js';

const MESSAGE = 300, TAIL = 1500;
const ISSUE_FORM = 'https://github.com/FlashML-org/FreeVideo/issues/new';

/**
 * Title, text and summary for the prefilled form. `where` is [English, Chinese] words for the place
 * ("Export", "导出"); `message` is the sentence the user sees; `facts` is an optional flat object of numbers and
 * states (never text or paths) that travels in the summary.
 */
export function issueDraft({where, message, kind = null, facts = null}, t = en => en) {
    const place = Array.isArray(where) ? where : [String(where || 'FreeVideo'), String(where || 'FreeVideo')];
    const clean = createErrorReport({exception_message: String(message || '')}).message;
    const lines = clean.split(/\r?\n/).filter(line => line.trim());
    // A traceback ends with the line that names the error; any other message starts with it.
    const first = (/^Traceback/.test(lines[0] || '') ? lines[lines.length - 1] : lines[0]) || '';
    const text = [t('FreeVideo showed an error.', 'FreeVideo 显示了一个错误。'),
        `Where: ${place[0]}`, `Error: ${first.trim().slice(0, MESSAGE)}`, ...(kind ? [`Kind: ${kind}`] : [])].join('\n');
    const tail = clean.length <= TAIL ? clean : clean.slice(0, 500) + '\n...\n' + clean.slice(-(TAIL - 505));
    return {title: `[Feedback] ${t(place[0], place[1])}`, text, summary: JSON.stringify(facts && Object.keys(facts).length ? {where: place[0], kind, tail, facts} : {where: place[0], kind, tail})};
}

// Browser-only modules, loaded as the button appears so the click opens the page at once
// (a page opened after waiting can be blocked as a pop-up).
let modules = null;
const load = () => modules || (modules = Promise.all([import('./feedback.js'), import('./updates.js')])
    .then(([feedback, updates]) => ({feedback, updates})).catch(() => null));

function icon() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false');
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', 'M13.5 2.5h-11a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h2v3l3.5-3h5.5a1 1 0 0 0 1-1v-7a1 1 0 0 0-1-1Z');
    svg.append(path);
    return svg;
}

let sheet = null;
function style() {
    if (sheet?.parentNode) return;
    sheet = document.createElement('style'); sheet.id = 'freevideo-issue-button-style';
    sheet.textContent = `.fv-issue-button{display:inline-flex;align-items:center;gap:6px;min-height:var(--fv-h-sm,32px);box-sizing:border-box;border:1px solid var(--fv-border,#45454a);border-radius:var(--fv-r-sm,8px);background:transparent;color:var(--fv-muted,#bbb);padding:4px 11px;font:inherit;font-size:13px;cursor:pointer}
.fv-issue-button:hover{color:var(--fv-ink,#eee);border-color:var(--fv-ink,#eee)}.fv-issue-button svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linejoin:round}
.fv-issue-button[hidden]{display:none!important}`;
    document.head.append(sheet);
}

/**
 * The button. `source` is {where, message, kind} or a function returning it when clicked (null: nothing to send).
 * `route` ({has, download, fetch, version}) is a run's diagnostic report: while it exists the click saves it and
 * the report tip opens the form, instead of the feedback form.
 */
export function issueButton(t, source, {className = '', route = null, open = (url, features) => globalThis.open?.(url, '_blank', features)} = {}) {
    style();
    const button = document.createElement('button'); button.type = 'button';
    button.className = `fv-issue-button ${className}`.trim();
    const label = t('Report a problem', '提交问题');
    const text = document.createElement('span'); text.textContent = label;
    button.append(icon(), text);
    load();
    button.onclick = async () => {
        const value = typeof source === 'function' ? source() : source;
        if (!value) return;
        if (route?.has()) {
            if (focusReportTip(button.parentElement)) return;
            try { await route.download(); } catch { return; }
            showReportTip(button.parentElement, button, t, {mode: 'downloaded', submit: () => reportIssue(route.fetch, route.version(), {save: false})});
            return;
        }
        const ready = await load();
        const draft = issueDraft(value, t);
        // Modules that did not load still leave the blank form reachable.
        if (!ready) return open(`${ISSUE_FORM}?${new URLSearchParams({template: 'feedback.yml', title: draft.title, feedback: draft.text})}`, 'noopener');
        open(ready.feedback.feedbackIssueURL(draft.text, ready.updates.productVersion(), draft.summary, draft.title), 'noopener');
    };
    return button;
}
