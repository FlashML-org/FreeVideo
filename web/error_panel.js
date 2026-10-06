// Shared by the Studio and graph view. Text only; never render exception HTML.
const reports = new WeakSet();
const captured = new WeakMap();
const diagnosticReports = new WeakSet();
const scalar = value => typeof value === 'string' ? value : typeof value === 'number' ? String(value) : '';
const list = value => Array.isArray(value) ? value : [];
const record = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};

export function errorText(detail) {
    if (typeof detail === 'string') return detail;
    if (reports.has(detail)) return detail.details;
    const response = record(detail?.response);
    const error = response.error ?? detail?.error;
    const rows = [scalar(error?.message) || scalar(error) || scalar(response.message)
        || scalar(detail?.exception_message) || scalar(detail?.message) || 'FreeVideo generation failed.'];
    if (scalar(detail?.node_id)) rows.unshift(`Node ${scalar(detail.node_id)} (${scalar(detail.node_type) || 'unknown'}):`);
    if (scalar(detail?.exception_type)) rows.unshift(detail.exception_type);
    if (scalar(error?.details)) rows.push(error.details);
    for (const [id, node] of Object.entries(record(response.node_errors ?? detail?.node_errors))) {
        rows.push(`Node ${id} (${scalar(node?.class_type) || 'unknown'}):`);
        for (const reason of list(node?.errors))
            rows.push('  - ' + [scalar(reason?.message), scalar(reason?.details)].filter(Boolean).join(': '));
    }
    const trace = list(detail?.traceback).map(scalar).join('') || scalar(detail?.traceback) || scalar(detail?.stack);
    if (trace) rows.push('', trace);
    return rows.join('\n');
}

function reportRedactor(detail, context) {
    // Input values are used only to remove echoed private content. They are
    // never included in the report, nor are workflow/inputs/extra_info objects.
    const hidden = new Set(), seen = new WeakSet();
    function remember(value) {
        if (typeof value === 'string' && value) {
            hidden.add(value);
            hidden.add(JSON.stringify(value).slice(1, -1));
            hidden.add(value.replace(/\\/g, '\\\\').replace(/'/g, "\\'")
                .replace(/\r/g, '\\r').replace(/\n/g, '\\n').replace(/\t/g, '\\t'));
            for (const line of value.split(/\r?\n/)) if (line.trim()) hidden.add(line.trim());
            if (/^\s*[\[{]/.test(value)) {
                try { remember(JSON.parse(value)); } catch { /* ordinary prompt text */ }
            }
        } else if (value && typeof value === 'object' && !seen.has(value)) {
            seen.add(value);
            for (const item of Object.values(value)) remember(item);
        }
    }
    remember(detail?.current_inputs);
    for (const row of Object.values(record(context.snapshot?.output)))
        for (const input of Object.values(record(row?.inputs))) {
            // API links contain node IDs, not private input text.
            if (Array.isArray(input) && input.length === 2 && Number.isInteger(input[1])) continue;
            remember(input);
        }
    for (const node of list(context.graph?._nodes)) {
        remember(node.properties?.freevideo_prompt_versions?.original);
        remember(node.properties?.freevideo_prompt_versions?.rewritten);
        for (const widget of list(node.widgets)) remember(widget.value);
    }
    remember(context.node?.properties?.freevideo_prompt_versions?.original);
    remember(context.node?.properties?.freevideo_prompt_versions?.rewritten);
    for (const widget of list(context.node?.widgets)) remember(widget.value);
    for (const node of Object.values(record(detail?.response?.node_errors ?? detail?.node_errors)))
        for (const reason of list(node?.errors)) remember(reason?.extra_info?.received_value);
    const values = [...hidden].sort((a, b) => b.length - a.length);
    return value => {
        let text = scalar(value);
        for (const secret of values) {
            // Short widget values (e.g. "a") must not erase every occurrence
            // inside exception names. Still remove standalone/quoted values.
            if (secret.length >= 4) text = text.split(secret).join('<INPUT REMOVED>');
            else {
                const escaped = secret.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                text = text.replace(new RegExp(`(^|[^\\p{L}\\p{N}_])${escaped}(?=$|[^\\p{L}\\p{N}_])`, 'gu'), '$1<INPUT REMOVED>');
            }
        }
        text = text.replace(/\b(?:https?|wss?|ftp|file):\/\/[^\s<>"']+/gi, '<URL REMOVED>')
            .replace(/\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+/gi, '<CREDENTIAL REMOVED>')
            .replace(/\b(?:hf_|sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{8,}\b/g, '<CREDENTIAL REMOVED>')
            .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, '<CREDENTIAL REMOVED>')
            .replace(/(["']?(?:[\w-]*(?:token|password|secret|credential|api[_-]?key)|authorization|cookie|username|hostname|computer_name)["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}]+)/gi, '$1<REDACTED>')
            .replace(/(["']?(?:prompt|negative_prompt|prompt_text|text)["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')/gi, '$1<INPUT REMOVED>')
            .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, '<EMAIL REMOVED>')
            .replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, '<ADDRESS REMOVED>');
        // Keep traceback source basenames and line numbers, never drive names,
        // home directories or private media filenames. Spaces in paths count.
        text = text.replace(/(^|[^A-Za-z0-9_:/<>])((?:[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/]|\/(?![\/\s]))[^\r\n"'<>;,\)\]]+)/gm, (_, prefix, path) => {
            const source = path.replace(/\\/g, '/').match(/\/([A-Za-z0-9_.-]+\.(?:py|js|ts|mjs))(?=[:\s]|$)/);
            return prefix + (source ? '<PATH>/' + source[1] + path.slice(path.indexOf(source[1]) + source[1].length) : '<PATH REMOVED>');
        });
        text = text.replace(/data:[^\s"'<>]+/gi, '<DATA REMOVED>');
        return text;
    };
}

export function createErrorReport(detail, context = {}) {
    if (reports.has(detail)) return detail;
    if (captured.has(detail)) return captured.get(detail);
    const clean = reportRedactor(detail, context);
    const nodeIdentifier = value => /^\d+(?::\d+)*$/.test(scalar(value)) ? scalar(value) : clean(scalar(value));
    const response = record(detail?.response);
    const sourceNodes = record(response.node_errors ?? detail?.node_errors);
    const nodes = Object.entries(sourceNodes).map(([id, node]) => ({
        id: nodeIdentifier(id), type: clean(scalar(node?.class_type)),
        errors: list(node?.errors).map(reason => ({
            type: clean(scalar(reason?.type)), message: clean(scalar(reason?.message)),
            details: clean(scalar(reason?.details)), input: clean(scalar(reason?.extra_info?.input_name)),
        })),
    }));
    const stage = context.stage || (Object.keys(response).length ? 'submission' : 'execution');
    const nodeId = scalar(detail?.node_id) || (stage === 'submission' ? scalar(context.node?.id) : '');
    const graphNode = context.graph?.getNodeById?.(nodeId);
    const nodeType = scalar(detail?.node_type) || scalar(graphNode?.comfyClass || graphNode?.type);
    if (!nodes.length && nodeId) nodes.push({id: nodeIdentifier(nodeId), type: clean(nodeType), errors: []});
    const inputs = context.snapshot?.output?.[context.node?.id]?.inputs
        || (nodeType === 'FreeVideoGenerate' ? detail?.current_inputs : null);
    const settings = {};
    for (const key of ['width', 'height', 'seconds', 'seed', 'two_pass', 'base_steps', 'refine_steps']) {
        const value = Array.isArray(inputs?.[key]) && inputs[key].length === 1 ? inputs[key][0] : inputs?.[key];
        if (typeof value === 'boolean' || typeof value === 'number' && Number.isFinite(value)) settings[key] = value;
    }
    const progress = {};
    for (const key of ['phase', 'stage']) if (typeof context.progress?.[key] === 'string') progress[key] = clean(context.progress[key]);
    for (const key of ['done', 'total']) if (Number.isFinite(context.progress?.[key])) progress[key] = context.progress[key];
    const report = {
        schema: 'freevideo.error-report', schema_version: 1, created_at: new Date().toISOString(),
        stage, message: clean(scalar(response.error?.message) || scalar(response.error) || scalar(response.message)
            || scalar(detail?.exception_message) || scalar(detail?.message) || scalar(detail) || 'FreeVideo generation failed.'),
        error_details: clean(scalar(response.error?.details)),
        exception_type: clean(scalar(detail?.exception_type) || scalar(response.error?.type) || scalar(detail?.name)),
        http_status: Number.isInteger(detail?.status) ? detail.status : null,
        nodes, settings, progress, details: clean(errorText(detail)),
        privacy: {local_only: true, workflow_and_inputs_included: false,
            media_and_logs_included: false,
            redacted: ['known input text', 'absolute paths', 'URLs', 'email addresses', 'common credentials'],
            note: 'Review the report before sharing. It contains the reported error, not a full installation diagnostic bundle.'},
    };
    reports.add(report);
    const failedNode = graphNode || (String(context.node?.id) === nodeId ? context.node : null);
    const reportId = failedNode?.freevideoReportId || context.progress?.report_id;
    if (stage === 'execution' && nodeType === 'FreeVideoGenerate' && /^[a-f0-9]{32}$/.test(reportId || ''))
        diagnosticReports.add(report);
    if (detail && typeof detail === 'object') captured.set(detail, report);
    return report;
}

// Match explicit failure signatures, not a guessed GPU/RAM capacity. Keep the
// original error separately so an explanation never replaces diagnostic data.
export function failureAdvice(value, t) {
    const text = String(value).slice(0, 65536);
    const row = (kind, title, summary, action) => ({kind, title: t(...title), summary: t(...summary), action: t(...action)});
    if (/Prompt outputs failed validation|Required input is missing|Value not in list|Return type mismatch|Failed to validate prompt|value (?:\S+ )?(?:smaller|bigger) than/i.test(text))
        return row('validation', ['Check the workflow inputs', '请检查工作流输入'],
            ['ComfyUI rejected the workflow before generation. The affected nodes and reasons are listed below.', 'ComfyUI 在生成开始前拒绝了工作流，相关节点和具体原因列在下方。'],
            ['Check the named inputs, connections and model choices. Copy the details or export the error report when asking for help.', '检查下方指出的输入、节点连接和模型选项；反馈时可复制详情或导出错误报告。']);
    if (/FreeVideo resource planning could not admit/i.test(text))
        return row('resources', ['The request exceeds available memory', '当前任务的可用内存不足'],
            ['Resource planning could not fit this request within the available GPU or system memory.', '资源规划发现当前任务超出了可用显存或系统内存预算。'],
            ['Close other memory-heavy applications, or select a lower resolution or shorter duration and retry. The details show which memory budget was insufficient.', '关闭其他占用内存的程序，或自行调低分辨率、缩短时长后重试。下方详情会指出不足的内存类型和差额。']);
    if (/commit headroom exhausted|paging file is too small|WinError 1455/i.test(text))
        return row('commit', ['Windows memory allocation limit reached', 'Windows 内存提交额度不足'],
            ['Windows cannot back another memory allocation, even if physical RAM is still available.', 'Windows 已没有足够的提交额度；这与物理 RAM 是否还有空闲是两回事。'],
            ['Close memory-heavy applications and retry. In Windows virtual memory settings, use a system-managed paging file on a drive with free space.', '关闭占用内存较多的程序后重试。在 Windows 虚拟内存设置中，使用系统管理的分页文件，并确保所在磁盘有空闲空间。']);
    if (/MPS backend out of memory|MPS (?:generation|encoding|decoding) reached|No unified-memory allowance|Native generation needs at least .* unified memory/i.test(text))
        return row('unified-memory', ['Unified memory is insufficient', '统一内存不足'],
            ['Mac CPU and GPU share memory. This stage exceeded the MPS allocation limit or the system memory safety threshold.', 'Mac 的 CPU 与 GPU 共用内存。这一阶段触及了 MPS 分配上限或系统内存保护线。'],
            ['Close memory-heavy applications and retry. Review any manual RAM limit or reserve; a lower resolution or shorter duration may also help. Export the report if it still fails.', '关闭占用内存较多的程序后重试，检查手动 RAM 限额和预留设置；也可降低分辨率或缩短时长。仍失败时请导出报告。']);
    if (/CUDA out of memory|torch\.OutOfMemoryError|cudaErrorMemoryAllocation/i.test(text))
        return row('vram', ['GPU memory allocation failed', '显存分配失败'],
            ['The GPU could not allocate the memory needed at this stage.', '这一阶段的显存申请没有成功。'],
            ['Close other GPU workloads and retry so FreeVideo can replan using the newly available memory. Your resolution, duration and steps are kept.', '关闭其他占用显卡的任务后重试，FreeVideo 会根据新的可用显存重新规划，保留你的分辨率、时长和步数。']);
    const ramReasons = /RAM guard:\s*([a-z_, ]+)\./i.exec(text)?.[1] || '';
    if (ramReasons.includes('system_or_commit_pressure') && !ramReasons.includes('working_memory_budget'))
        return row('ram', ['System memory headroom is too low', '系统可用内存不足'],
            ['Memory headroom fell below the safety threshold during execution, so generation stopped. Earlier planning values may be higher.', '运行时测得的系统内存余量低于保护线，生成已停止。此前资源规划时的数值可能更高。'],
            ['Close memory-heavy applications and duplicate FreeVideo/ComfyUI instances, then retry. Export the report if it still fails.', '关闭占用内存较多的程序及重复运行的 FreeVideo／ComfyUI 后重试。仍失败时请导出报告。']);
    if (ramReasons.includes('working_memory_budget') && !ramReasons.includes('system_or_commit_pressure'))
        return row('ram', ['The configured RAM limit was reached', '已触及设置的 RAM 限额'],
            ['The worker exceeded the enforced RAM budget from a manual limit or selected profile.', '生成进程超过了手动限额或所选配置规定的 RAM 预算。'],
            ['Review the RAM limit or selected profile. Automatic mode replans from available memory. Export the report if it still fails.', '检查 RAM 限额或所选配置；自动模式会根据可用内存重新规划。仍失败时请导出报告。']);
    if (/RAM guard:|crossed its RAM budget|Insufficient currently available memory/i.test(text))
        return row('ram', ['Available memory or an explicit RAM limit reached', '可用内存或手动 RAM 限额不足'],
            ['Automatic RAM estimates can be exceeded while memory is available. This stop indicates system pressure or an explicitly enforced limit.', '自动模式允许在系统仍有余量时超出预估 RAM；停止意味着系统内存压力，或触及了手动设置的硬限制。'],
            ['Close memory-heavy applications and retry. If you set a RAM limit for testing, remove or raise it for normal generation. Copy the details below if it still fails.', '关闭占用内存较多的程序后重试。如果设置过测试用 RAM 限制，正常生成时可取消或调高。仍失败时可复制下方详情。']);
    if (/No space left on device|WinError 112|disk (?:is )?full/i.test(text))
        return row('disk', ['Disk space is insufficient', '磁盘空间不足'],
            ['FreeVideo could not write a required file.', 'FreeVideo 无法写入所需文件。'],
            ['Free space on the installation/output drive and retry. Keep model downloads and retained outputs if you want to resume or inspect them.', '清理安装目录或输出目录所在磁盘的空间后重试；保留模型下载和输出文件，便于续传或排查。']);
    if (/Model download paused|unexpected-transfer-size|ConnectionError|ConnectTimeout|ReadTimeout|Every download source failed|All Git sources failed|All package sources failed|curl-28/i.test(text))
        return row('download', ['Download needs attention', '下载未完成'],
            ['A transfer failed or its received data did not match the expected file.', '传输中断，或收到的数据与预期文件不一致。'],
            ['Check your connection or select another download source in Settings, then retry. Existing download fragments are retained.', '检查网络，或在设置中切换下载源后重试。已有下载片段会保留。']);
    if (/ModuleNotFoundError|No module named|DLL load failed/i.test(text))
        return row('dependencies', ['A runtime dependency could not load', '运行依赖无法加载'],
            ['The runtime may be incomplete or an incompatible environment may have been selected.', '运行环境可能不完整，或使用了不兼容的环境。'],
            ['Open the FreeVideo launcher and rerun installation for the same folder to check dependencies. If it persists, copy the details below.', '打开 FreeVideo 启动器，对同一安装目录重新执行安装以检查依赖。仍失败时可复制下方详情。']);
    return row('unknown', ['Generation stopped', '生成已停止'],
        ['This request did not complete. The exact error is retained below.', '这次请求未完成，具体错误已保留在下方。'],
        ['Copy the error details when reporting the issue; they include the failed stage when available.', '反馈问题时请复制错误详情，其中会保留已记录的失败阶段。']);
}

let activeDialog = null;
function showFailureDialog(report, t) {
    // Both graph and Studio receive execution_error. Show one modal for that
    // event, above the Studio modal if it is open, with keyboard dismissal.
    const identity = report.stage + '\n' + report.details;
    if (activeDialog?.open && activeDialog.freevideoError === identity) return;
    activeDialog?.close();
    const dialog = document.createElement('dialog'); dialog.className = 'fv-failure-dialog';
    dialog.freevideoError = identity;
    dialog.setAttribute('aria-label', t('FreeVideo error', 'FreeVideo 错误'));
    const panel = createErrorPanel(t); panel.show(report, false);
    const close = document.createElement('button'); close.type = 'button';
    close.textContent = t('Got it', '知道了'); close.autofocus = true;
    close.onclick = () => dialog.close();
    dialog.append(panel.element, close);
    dialog.onclose = () => { if (activeDialog === dialog) activeDialog = null; dialog.remove(); };
    document.body.append(dialog);
    if (typeof dialog.showModal !== 'function') { dialog.remove(); return; }
    activeDialog = dialog; dialog.showModal();
}

export function createErrorPanel(t) {
    if (!document.getElementById('freevideo-error-style')) {
        const style = document.createElement('style'); style.id = 'freevideo-error-style';
        style.textContent = `.fv-failure{border:1px solid var(--fv-danger,#dc7c7c);border-radius:var(--fv-r-md,12px);padding:12px 14px;margin:10px 0;text-align:left;min-width:0}
.fv-failure[hidden],.fv-failure button[hidden]{display:none!important}.fv-failure-head{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:8px}.fv-failure-actions{display:flex;flex-wrap:wrap;gap:8px}
.fv-failure textarea{box-sizing:border-box;width:100%;height:180px;min-height:100px;resize:vertical;border:1px solid var(--fv-border,#45454a);border-radius:var(--fv-r-sm,8px);background:var(--fv-bg,#202126);color:var(--fv-ink,#eee);font:12px/1.5 monospace;padding:9px;white-space:pre-wrap}
.fv-failure button,.fv-failure-dialog>button{min-height:var(--fv-h-sm,32px);border:1px solid var(--fv-border,#45454a);border-radius:var(--fv-r-sm,8px);background:var(--fv-raised,#303138);color:var(--fv-ink,#eee);padding:5px 12px;font:inherit;font-weight:var(--fv-medium,500);cursor:pointer}
.fv-failure p{font-size:14px;line-height:1.6;margin:8px 0}.fv-failure summary{cursor:pointer;margin-top:12px}.fv-failure .fv-failure-cause{white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto}.fv-failure .fv-failure-privacy{font-size:12px;color:var(--fv-muted,#bbb)}
.fv-failure-dialog{box-sizing:border-box;width:min(560px,94vw);max-height:88vh;overflow:auto;padding:22px 24px;border:1px solid var(--fv-border,#45454a);border-radius:var(--fv-r-lg,16px);background:var(--fv-bg,#202126);color:var(--fv-ink,#eee);box-shadow:0 24px 80px #0008}
.fv-failure-dialog::backdrop{background:#070a0db8;backdrop-filter:blur(6px)}.fv-failure-dialog>.fv-failure{border:0;margin:0 0 16px;padding:0}.fv-failure-dialog>button{display:block;margin-left:auto;background:var(--fv-accent,#6db8fa);border-color:var(--fv-accent,#6db8fa);color:var(--fv-bg,#111720);font-weight:var(--fv-semibold,600)}`;
        document.head.append(style);
    }
    const element = document.createElement('section'); element.className = 'fv-failure'; element.hidden = true;
    const head = document.createElement('div'); head.className = 'fv-failure-head';
    const title = document.createElement('strong'); title.textContent = t('Generation failed', '生成失败');
    const copy = document.createElement('button'); copy.type = 'button'; copy.textContent = t('Copy error', '复制错误');
    const download = document.createElement('button'); download.type = 'button'; download.textContent = t('Export report', '导出报告');
    const actions = document.createElement('div'); actions.className = 'fv-failure-actions';
    actions.append(copy, download);
    const explanation = document.createElement('p'), action = document.createElement('p');
    const location = document.createElement('p'), cause = document.createElement('p'); cause.className = 'fv-failure-cause';
    const privacy = document.createElement('p'); privacy.className = 'fv-failure-privacy';
    privacy.textContent = t('Known input text, paths and common credentials are removed. Export saves a local JSON file; review it before sharing.',
        '已去除已知输入文本、路径和常见凭据。导出会保存为本地 JSON 文件，分享前可查看内容。');
    const downloadStatus = document.createElement('p'); downloadStatus.setAttribute('role', 'status');
    let currentReport = null;
    const disclosure = document.createElement('details'), summary = document.createElement('summary');
    summary.textContent = t('Technical details', '技术详情');
    const details = document.createElement('textarea'); details.readOnly = true;
    details.setAttribute('aria-label', t('Error details', '错误详情')); details.spellcheck = false;
    copy.onclick = async () => {
        let copied = false;
        try { await navigator.clipboard.writeText(details.value); copied = true; } catch {
            // Remote ComfyUI commonly uses plain HTTP, where Clipboard API is
            // unavailable. Keep selectable text even if the browser denies copy.
            disclosure.open = true; details.focus(); details.select();
            try { copied = document.execCommand('copy'); } catch { /* manual Ctrl+C */ }
        }
        copy.textContent = copied ? t('Copied', '已复制') : t('Press Ctrl+C', '请按 Ctrl+C');
    };
    download.onclick = () => {
        if (!currentReport) return;
        let url, link;
        try {
            const payload = JSON.stringify(currentReport, null, 2) + '\n';
            url = URL.createObjectURL(new Blob([payload], {type: 'application/json;charset=utf-8'}));
            link = document.createElement('a'); link.href = url;
            link.download = 'freevideo-error-' + currentReport.created_at.replace(/[:.]/g, '-') + '.json';
            document.body.append(link); link.click();
            downloadStatus.textContent = t('Error summary download started.', '已开始下载错误摘要。');
        } catch {
            // Keep the same redacted JSON available even if downloads are
            // disabled by the browser or the page is served over plain HTTP.
            disclosure.open = true; details.value = JSON.stringify(currentReport, null, 2);
            details.focus(); details.select();
            downloadStatus.textContent = t('Download unavailable. Use Copy error to copy the report below.', '浏览器未能下载，请点击“复制错误”复制下方完整报告。');
        } finally {
            link?.remove();
            if (url) setTimeout(() => URL.revokeObjectURL(url), 30000);
        }
    };
    disclosure.append(summary, details);
    head.append(title, actions); element.append(head, explanation, location, cause, action, disclosure, privacy, downloadStatus);
    return {element, show(value, popup = true, context = {}) {
        const report = createErrorReport(value, context); currentReport = report;
        const advice = failureAdvice(report.details, t);
        if (report.stage === 'submission' && advice.kind === 'unknown') {
            advice.title = t('The task could not be submitted', '任务提交失败');
            advice.summary = t('The task submission returned an error. Its details are shown below.', '提交任务时返回了错误，具体原因显示在下方。');
        }
        if (report.stage === 'queue' && advice.kind === 'unknown') {
            advice.title = t('The queue operation did not complete', '队列操作未完成');
            advice.summary = t('Check that ComfyUI is still running and connected, then retry.', '请确认 ComfyUI 仍在运行、连接正常后重试。');
        }
        title.textContent = advice.title; explanation.textContent = advice.summary; action.textContent = advice.action;
        const stage = report.stage === 'submission' ? t('Submitting task', '提交任务')
            : report.stage === 'queue' ? t('Queue operation', '队列操作') : t('Executing workflow', '执行工作流');
        location.textContent = stage + (report.http_status ? ` · HTTP ${report.http_status}` : '')
            + report.nodes.map(node => ` · ${node.type || t('Node', '节点')} #${node.id}`).join('');
        const reasons = report.nodes.flatMap(node => node.errors.map(error =>
            `${node.type || t('Node', '节点')} #${node.id}${error.input ? ' · ' + error.input : ''}: ${error.message}${error.details ? ': ' + error.details : ''}`));
        cause.textContent = reasons.length ? reasons.join('\n') : [report.message, report.error_details].filter(Boolean).join('\n');
        details.value = report.details; disclosure.open = false; element.hidden = false;
        downloadStatus.textContent = '';
        download.hidden = diagnosticReports.has(report);
        privacy.textContent = download.hidden
            ? t('Use Download report in the preview or node panel for the diagnostic report.', '完整诊断请使用预览区或节点面板中的“下载报告”。')
            : t('Known input text, paths and common credentials are removed. Export saves a local error summary; review it before sharing.',
                '已去除已知输入文本、路径和常见凭据。导出会保存本地错误摘要，分享前可查看内容。');
        copy.textContent = t('Copy error', '复制错误');
        if (popup) showFailureDialog(report, t);
        return report;
    }, clear() { currentReport = null; details.value = ''; element.hidden = true; }};
}
