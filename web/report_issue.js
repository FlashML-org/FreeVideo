// Send the redacted diagnostic report as a GitHub issue: the report menu's
// entry, and a tip above that menu after a finished video, shown until the
// user closes it once.
const ISSUE_FORM = 'https://github.com/FlashML-org/FreeVideo/issues/new';
const TIP_KEY = 'freevideo.report-tip';

const gb = bytes => Number.isFinite(bytes) && bytes > 0 ? Math.round(bytes / 2 ** 30) : null;

// The issue form, prefilled from the redacted report. The report carries no
// prompt, reference or video, so neither does the address.
export function reportIssueURL(report, productVersion = null) {
    const analysis = report?.analysis || {};
    const hardware = analysis.hardware || {};
    const geometry = analysis.geometry || {};
    const gpu = String(hardware.gpu_name || '').replace(/^NVIDIA\s+(GeForce\s+)?/i, '').trim();
    // Cards are sold in whole gigabytes; installed RAM shows a little under
    // its size, so it keeps a decimal rather than rounding down a size.
    const vram = gb(hardware.vram_total);
    const ram = Number.isFinite(hardware.ram_total) && hardware.ram_total > 0
        ? (hardware.ram_total / 2 ** 30).toFixed(1).replace(/\.0$/, '') : null;
    const card = gpu ? gpu + (vram ? ` ${vram} GB` : '') : '';
    const seconds = geometry.frames && geometry.fps ? Math.round(geometry.frames / geometry.fps * 10) / 10 : null;
    const video = [geometry.width && geometry.height ? `${geometry.width}×${geometry.height}` : '',
                   seconds ? `${seconds} s` : ''].filter(Boolean).join(' · ');
    const total = report?.summary?.request_seconds;
    const fields = {
        template: 'report.yml',
        title: ['[Report]', [card, video].filter(Boolean).join(' · ')].join(' ').trim(),
        version: [productVersion ? `v${productVersion}` : '', analysis.version ? `(${analysis.version})` : ''].filter(Boolean).join(' '),
        hardware: [card, ram ? `${ram} GB RAM` : '', hardware.system || ''].filter(Boolean).join(' · '),
        request: video,
        timing: Number.isFinite(total) && total > 0 ? `${Math.round(total)} s` : '',
    };
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(fields)) if (value) params.set(key, value);
    return `${ISSUE_FORM}?${params}`;
}

// Each download is named by when it was saved, so saving reports again never
// leaves "video.debug (1).json"-style copies; the card names the file just saved.
let savedName = null;
export function reportFileName(date = new Date()) {
    const pad = value => String(value).padStart(2, '0');
    return `FreeVideo-report-${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}`
        + `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}.json`;
}

export function saveReport(blob) {
    const url = URL.createObjectURL(blob);
    savedName = reportFileName();
    const link = document.createElement('a'); link.href = url; link.download = savedName;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return savedName;
}

// Open the prefilled form, saving the report first unless the user just
// downloaded it; GitHub cannot take a file from a link, so the user drags the
// saved file in. Resolves to the form's address.
export async function reportIssue(fetchReport, productVersion = null, {save = true, open = url => window.open(url, '_blank', 'noopener')} = {}) {
    const blob = await fetchReport();
    if (save) saveReport(blob);
    let report = null;
    try { report = JSON.parse(await blob.text()); } catch { /* The form still opens, without prefilled fields. */ }
    const address = reportIssueURL(report, productVersion);
    open(address);
    return address;
}

export function reportTipClosed() {
    try { return localStorage.getItem(TIP_KEY) === '1'; } catch { return false; }
}

function closeForGood() {
    try { localStorage.setItem(TIP_KEY, '1'); } catch { /* Shown again after the next video. */ }
}

const el = (tag, cls, text) => {
    const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text;
    return e;
};

const ICON = {
    report: '<path d="M9.5 2H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.5L9.5 2Z"/><path d="M9.5 2v2.5H12M6.5 8h3M6.5 10.5h3"/>',
    done: '<path d="m3.8 8.4 2.7 2.7 5.7-6"/>',
    close: '<path d="m4.5 4.5 7 7m0-7-7 7"/>',
    external: '<path d="M6.5 3.5h-3v9h9v-3M9 2.5h4.5V7M13.5 2.5 7.5 8.5"/>',
    lock: '<rect x="3.5" y="7" width="9" height="6.5" rx="1.5"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/>',
};
const glyph = (name, cls = 'fv-report-tip-glyph') => {
    const span = el('span', cls); span.setAttribute('aria-hidden', 'true');
    span.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${ICON[name]}</svg>`;
    return span;
};

const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

// A card on its own row under the result's action bar, so it never covers the
// video, with a notch under the Report button. It opens and closes by height,
// so the picture above eases instead of jumping. `mode` is 'invite' (after a
// finished video: Submit saves the report too) or 'downloaded' (the user just
// saved it: Submit only opens the form). `submit` resolves to the form's
// address; `onHide` runs when the user closes the card, not on a redraw;
// `animate: false` redraws a card that was already showing.
export function showReportTip(bar, anchor, t, {mode = 'invite', submit, onHide = null, animate = true} = {}) {
    bar.parentElement.querySelector(':scope > .fv-report-tip:not(.fv-report-tip-leaving)')?.remove();
    const tip = el('div', 'fv-report-tip'); tip.setAttribute('role', 'region');
    tip.setAttribute('aria-label', t('Send the diagnostic report', '提交诊断报告'));
    const card = el('div', 'fv-report-tip-card');
    const mark = el('span', 'fv-report-tip-mark'); mark.setAttribute('aria-hidden', 'true');
    const text = el('div', 'fv-report-tip-text');
    const title = el('strong', 'fv-report-tip-title');
    const lead = el('p', 'fv-report-tip-lead');
    const note = el('small', 'fv-report-tip-note');
    // `fv-actions` lends the row the result bar's button styles.
    const row = el('div', 'fv-report-tip-actions fv-actions');
    const close = el('button', 'fv-report-tip-close'); close.type = 'button'; close.setAttribute('aria-label', t('Close', '关闭'));
    close.append(glyph('close'));
    const hide = () => {
        anchor.classList.remove('fv-report-attention'); onHide?.();
        if (reducedMotion() || typeof tip.animate !== 'function' || !tip.isConnected) { tip.remove(); return; }
        tip.classList.add('fv-report-tip-leaving');
        tip.animate([{maxHeight: `${tip.offsetHeight}px`, paddingTop: '6px', opacity: 1}, {maxHeight: '0px', paddingTop: '0px', opacity: 0}],
            {duration: 240, easing: 'cubic-bezier(.4,0,.2,1)', fill: 'forwards'}).finished.then(() => tip.remove(), () => tip.remove());
    };
    const dismiss = () => { closeForGood(); hide(); };
    close.onclick = dismiss;
    function sent(address) {
        tip.classList.add('fv-report-tip-sent'); anchor.classList.remove('fv-report-attention');
        mark.replaceChildren(glyph('done'));
        title.textContent = t('Last step', '最后一步');
        lead.textContent = savedName
            ? t(`Please drag ${savedName} into "Diagnostic report", then select Submit new issue.`,
                `请将 ${savedName} 拖入「诊断报告」，再点击 Submit new issue 即可。`)
            : t('Please drag the downloaded report into "Diagnostic report", then select Submit new issue.',
                '请将下载的诊断报告拖入「诊断报告」，再点击 Submit new issue 即可。');
        const link = el('a', null, t("Page didn't open? Go to GitHub", '页面没有打开？点此前往 GitHub'));
        link.href = address; link.target = '_blank'; link.rel = 'noopener';
        note.replaceChildren(link);
        const done = el('button', 'fv-action fv-action-secondary', t('Done', '完成')); done.type = 'button';
        done.onclick = dismiss;
        row.replaceChildren(done);
        close.remove();
        done.focus({preventScroll: true});
    }
    mark.append(glyph('report'));
    if (mode === 'downloaded') {
        title.textContent = t('Diagnostic report downloaded', '诊断报告已下载');
    } else {
        title.textContent = t('Share a diagnostic report?', '欢迎提交诊断报告');
    }
    lead.textContent = t("We'll optimize specifically for your setup, and it helps other users too.",
        '我们会针对您的硬件配置进行专门优化，也能帮助其他用户。');
    note.append(glyph('lock', 'fv-report-tip-lock'), t('Hardware, settings and timings only. No prompts, images or videos.',
        '仅包含硬件信息、生成参数和耗时，不含提示词、参考图或视频。'));
    const send = el('button', 'fv-action fv-action-primary'); send.type = 'button';
    const sendText = el('span', null, t('Submit on GitHub', '前往 GitHub 提交'));
    send.append(sendText, glyph('external'));
    send.title = t('Opens the GitHub issue form', '打开 GitHub 上的 issue 页面');
    send.onclick = async () => {
        if (send.disabled) return;
        send.disabled = true; sendText.textContent = t('Preparing…', '正在整理…');
        try { const address = await submit(); closeForGood(); sent(address); }
        catch { send.disabled = false; sendText.textContent = t('Report unavailable · retry', '报告暂不可用 · 重试'); }
    };
    row.append(send);
    text.append(title, lead, note);
    card.append(mark, text, row, close);
    tip.append(card);
    tip.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        event.preventDefault(); event.stopPropagation(); dismiss(); anchor.focus();
    });
    if (mode === 'invite') anchor.classList.add('fv-report-attention');
    bar.after(tip);
    // The notch sits under the Report button's centre.
    const notch = anchor.getBoundingClientRect(), box = card.getBoundingClientRect();
    tip.style.setProperty('--fv-tip-notch', `${Math.max(18, Math.min(box.width - 18, notch.left + notch.width / 2 - box.left))}px`);
    // Open from nothing to the card's own height, so the picture eases up.
    if (animate && !reducedMotion() && typeof tip.animate === 'function') {
        tip.animate([{maxHeight: '0px', paddingTop: '0px', opacity: 0}, {maxHeight: `${tip.offsetHeight}px`, paddingTop: '6px', opacity: 1}],
            {duration: 340, easing: 'cubic-bezier(.22,1,.36,1)'});
    }
    return tip;
}
