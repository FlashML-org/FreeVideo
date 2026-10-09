import { api } from "../../scripts/api.js";
import { wordmark } from './wordmark.js';
import { openCompatibility } from './compatibility.js';
import { animateDetails, closeDialog } from './motion.js';

const css = document.createElement('link');
css.rel = 'stylesheet'; css.href = new URL('./setup.css', import.meta.url).href;
document.head.append(css);

const languageOverride = typeof location !== 'undefined'
    ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en'
    && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
const gib = n => Number.isFinite(n) ? (n / 2 ** 30).toFixed(1) + " GiB" : "—";
let current;

export async function openSetup() {
    if (current?.open) { current.focus(); return; }
    const dialog = document.createElement("dialog"); current = dialog;
    dialog.className = "fv-setup";
    const node = (tag, label) => { const e = document.createElement(tag); if (label) e.textContent = label; return e; };
    const note = label => { const e = node("p", label); e.className = "fv-muted"; return e; };
    const heading = node('header'); heading.className = 'fv-setup-head';
    const dismiss = node('button', '×'); dismiss.type = 'button'; dismiss.className = 'fv-close';
    dismiss.setAttribute('aria-label', t('Close', '关闭')); dismiss.onclick = () => closeDialog(dialog);
    heading.append(wordmark(), node('h2', t('Settings', '设置')), dismiss);
    dialog.setAttribute('aria-label', t('FreeVideo settings', 'FreeVideo 设置'));
    const installation = node('details'); installation.open = true;
    installation.append(node('summary', t('Installation & model folders', '安装与模型目录')));
    const inputs = node("div"); installation.append(inputs);
    const rootLabel = node("label", t("FreeVideo installation folder", "FreeVideo 安装目录"));
    const root = node("input"); root.type = "text"; rootLabel.append(root); inputs.append(rootLabel);
    const extraLabel = node("label", t("Additional model folders (optional, one per line)", "其他模型目录（可选，每行一个）"));
    const extra = node("textarea"); extra.rows = 2; extraLabel.append(extra); inputs.append(extraLabel);
    const libraries = node("details"); libraries.append(node("summary", t("Automatically detected model folders", "自动发现的模型目录")));
    const paths = node("pre"); libraries.append(paths); inputs.append(libraries);
    const copyLabel = node("label"); copyLabel.className = 'fv-check'; const copy = node("input"); copy.type = "checkbox";
    copyLabel.append(copy, node('span', t("Create independent copies (uses extra disk space)", "创建独立副本（额外占用磁盘）"))); inputs.append(copyLabel);
    inputs.append(note(t('Models are reused by default.', '默认直接复用已有模型。')));
    const samplingLabel = node('label'); samplingLabel.className = 'fv-check';
    const samplingCaches = node('input'); samplingCaches.type = 'checkbox';
    const samplingText = node('span', t('Prepare all quality levels (optional)', '提前下载全部质量档位（可选）'));
    samplingLabel.append(samplingCaches, samplingText); inputs.append(samplingLabel);
    const downloads = node('section');
    downloads.append(node('h3', t('Downloads', '下载')));
    const modeLabel = node('label', t('Connection', '连接模式'));
    const proxyMode = node('select');
    for (const [id, name] of [['auto', t('Auto (recommended)', '自动（推荐）')], ['proxy', t('Proxy only', '仅代理')], ['direct', t('Direct only', '仅直连')]]) {
        const option = node('option', name); option.value = id; proxyMode.append(option);
    }
    const modeHelp = note('');
    modeLabel.append(proxyMode);
    downloads.append(modeLabel, modeHelp);
    const sourceLabel = node('label', t('Download source', '下载源'));
    const source = node('select');
    for (const [id, name] of [['auto',t('Automatic','自动选择')],['official','Hugging Face'],['hf-mirror','HF Mirror'],['modelscope','ModelScope']]) {
        const option = node('option',name); option.value = id; source.append(option);
    }
    sourceLabel.append(source); downloads.append(sourceLabel);
    const probeButton = node('button', t('Test source speeds', '重新测速'));
    const sourceStatus = note(''), speeds = node('div');
    downloads.append(probeButton, sourceStatus, speeds,
        note(t('Switching applies to the current download; compatible fragments resume. Speed tests do not switch.', '切换立即作用于当前下载，兼容断点会续传。仅测速不切换。')));
    const auth = node('details');
    auth.append(node('summary', t('Hugging Face token · optional', 'Hugging Face Token · 可选')));
    auth.append(note(t('Use your account’s download quota instead of the anonymous limit. Kept for this session only.', '使用账号下载额度，避开匿名限流。仅本次会话有效。')));
    const hfTokenLabel = node('label', 'Hugging Face Token');
    const hfToken = node('input'); hfToken.type = 'password'; hfToken.autocomplete = 'off'; hfToken.spellcheck = false;
    hfTokenLabel.append(hfToken); auth.append(hfTokenLabel);
    const authActions = node('div'); authActions.className = 'fv-actions';
    const applyToken = node('button', t('Apply token', '应用 Token'));
    const clearToken = node('button', t('Clear session token', '清除本次 Token'));
    const tokenLink = node('a', t('Get a read token ↗', '获取读取 Token ↗'));
    tokenLink.href = 'https://huggingface.co/settings/tokens'; tokenLink.target = '_blank'; tokenLink.rel = 'noopener noreferrer';
    authActions.append(applyToken, clearToken, tokenLink); auth.append(authActions);
    const authStatus = note(''); auth.append(authStatus); downloads.append(auth);
    const advanced = node('details'); advanced.append(node('summary', t('Advanced settings', '高级设置')));
    const compatibility = node('button', t('Compatibility settings', '兼容性设置'));
    compatibility.onclick = openCompatibility;
    advanced.append(compatibility);
    const resources = node('details');
    const reserveSummary = node('summary', t('Reserved VRAM', '预留显存'));
    resources.append(reserveSummary);
    const reserveLabel = node('label', t('Reserved VRAM', '预留显存'));
    const reserveAuto = node('input'); reserveAuto.type = 'checkbox'; reserveAuto.checked = true;
    const autoLabel = node('label'); autoLabel.className = 'fv-check'; autoLabel.append(reserveAuto, node('span', t('Automatic optimization', '自动优化')));
    const reserve = node('input'); reserve.type = 'number'; reserve.min = '0.2'; reserve.step = '0.1';
    reserve.value = '1.0'; reserve.disabled = true; reserve.className = 'fv-number';
    reserveLabel.append(reserve, document.createTextNode('GiB'));
    const saveReserve = node('button', t('Save', '保存')), reserveStatus = note('');
    const reserveHelp = note(t('Keep this much additional VRAM free. Applies to new requests; current usage is already accounted for.', '额外保留的空闲显存。对后续请求生效；其他程序当前的占用已单独计入。'));
    resources.append(autoLabel, reserveLabel, reserveHelp, saveReserve, reserveStatus);
    advanced.append(resources);
    reserveAuto.onchange = () => { reserve.disabled = reserveAuto.checked; };
    // Placement calibration. A measurement, not a setting: it changes nothing
    // about this installation, it reports what this machine measures so the
    // choices that are currently limited to one platform can be settled.
    const calibration = node('details');
    calibration.append(node('summary', t('Performance measurement', '性能测量')));
    const calibrateRepeats = node('input'); calibrateRepeats.type = 'number';
    calibrateRepeats.min = '1'; calibrateRepeats.max = '4'; calibrateRepeats.step = '1';
    calibrateRepeats.value = '1';
    calibrateRepeats.className = 'fv-number';
    const repeatsLabel = node('label', t('Passes', '轮数'));
    repeatsLabel.append(calibrateRepeats);
    const calibrateButton = node('button', t('Run placement test', '运行放置测试'));
    const calibrateStatus = note('');
    calibration.append(repeatsLabel, note(t(
        'Measures the placement choices this machine can settle and writes a report. It does not change your settings. The card stays busy for roughly half an hour per pass and generation cannot run meanwhile.',
        '测量本机能定下来的放置选择并生成报告，不会改动你的设置。每轮大约占用显卡半小时，期间无法生成。')),
        calibrateButton, calibrateStatus);
    advanced.append(calibration);
    const state = node("p"); state.setAttribute("role", "status");
    const overall = node("div"); overall.hidden = true;
    const overallText = node("p"); const overallBar = node("progress"); overallBar.value = 0; overallBar.max = 1;
    overall.append(overallText, overallBar);
    const bar = node("progress"); bar.hidden = true; bar.value = 0;
    const counters = node("p");
    const detail = note(""); const plan = node("div");
    const more = node("details"); more.append(node("summary", t("Details and retained logs", "详细信息与保留日志"))); const log = node("pre"); more.append(log);
    installation.append(overall, state, bar, counters, detail, plan, more);
    const acceptLabel = node("label"); acceptLabel.className = 'fv-check'; const accept = node("input"); accept.type = "checkbox";
    acceptLabel.append(accept, node('span', t("I accept the displayed installation plan and model/toolkit licenses", "我同意当前展示的安装计划和模型／工具包许可证")));
    acceptLabel.hidden = true; installation.append(acceptLabel);
    const actions = node("div"); actions.className = "fv-actions";
    const inspect = node("button", t("Detect & review", "检测并预览")); inspect.className = "fv-primary";
    const use = node("button", t("Use existing installation", "使用已有安装"));
    const install = node("button", t("Install / repair", "安装／修复")); install.disabled = true;
    const cancel = node("button", t("Stop setup", "停止安装")); cancel.disabled = true;
    actions.append(inspect, use, install, cancel); installation.append(actions);
    // Why Install / repair is unavailable, so it never looks like a broken button.
    const blocked = note(""); blocked.classList.add("fv-setup-why"); blocked.setAttribute("aria-live", "polite"); blocked.hidden = true;
    installation.append(blocked);
    dialog.append(heading, installation, downloads, advanced);
    const motions = [...dialog.querySelectorAll('details')].map(animateDetails);
    dialog.addEventListener('cancel', e => { e.preventDefault(); closeDialog(dialog); });
    document.body.append(dialog); dialog.showModal();
    let token, planId, busy = false, probeBusy = false, pollTimer, lastPlanId, planErrors = false, planStale = false, ready = false;
    const fail = error => { installation.open = true; state.textContent = error.message; state.className = "fv-failure"; state.scrollIntoView({block:'nearest'}); };
    const request = async (action, value) => {
        const response = await api.fetchApi("/freevideo/setup" + (action ? "/" + action : root.value ? '?root='+encodeURIComponent(root.value) : ""), action ? {
            method: "POST", headers: { "Content-Type": "application/json", "X-FreeVideo-Setup": token }, body: JSON.stringify(value || {}),
        } : {});
        const result = await response.json(); if (!response.ok || result.error) throw new Error(result.error || response.statusText); return result;
    };
    let reserveField = 'gpu_reserve_gib';
    const showResources = value => {
        const unified = value && Object.hasOwn(value, 'ram_reserve_gib');
        reserveField = unified ? 'ram_reserve_gib' : 'gpu_reserve_gib';
        reserveSummary.textContent = unified ? t('Reserved unified memory', '预留统一内存') : t('Reserved VRAM', '预留显存');
        reserveLabel.firstChild.textContent = reserveSummary.textContent;
        reserve.min = unified ? '1' : '0.2';
        reserveAuto.checked = value?.[reserveField] == null;
        reserve.disabled = reserveAuto.checked;
        reserve.value = reserveAuto.checked ? (unified ? '2.0' : '1.0') : String(value[reserveField]);
        reserveHelp.textContent = unified
            ? t('CPU and GPU share unified memory. Keep this much additional RAM available for macOS and other applications. Applies to new requests.', 'CPU 与 GPU 共用统一内存。额外保留这些 RAM 供 macOS 和其他程序使用，对后续请求生效。')
            : t('Keep this much additional VRAM free. Applies to new requests; current usage is already accounted for.', '额外保留的空闲显存。对后续请求生效；其他程序当前的占用已单独计入。');
    };
    calibrateButton.onclick = async () => {
        if (!calibrateRepeats.checkValidity()) { calibrateRepeats.reportValidity(); return; }
        calibrateButton.disabled = true;
        calibrateStatus.className = 'fv-muted';
        calibrateStatus.textContent = t('Starting…', '正在启动…');
        try {
            render(await request('calibrate', {repeats: Number(calibrateRepeats.value)}));
            poll();
            calibration.open = true;
            calibrateStatus.textContent = t('Running. Progress is below; closing this panel keeps it running.',
                                            '正在运行。进度见下方；关闭面板不会中断。');
        } catch (error) {
            calibrateStatus.className = 'fv-failure';
            calibrateStatus.textContent = error.message;
        } finally { calibrateButton.disabled = false; }
    };
    saveReserve.onclick = async () => {
        if (!reserveAuto.checked && (!reserve.value || !reserve.checkValidity())) { reserve.reportValidity(); return; }
        saveReserve.disabled = true;
        try {
            showResources(await request('resources', {[reserveField]: reserveAuto.checked ? null : Number(reserve.value)}));
            reserveStatus.textContent = t('Saved · applies to the next request', '已保存 · 下次生成生效');
        } catch (error) { reserveStatus.textContent = error.message; }
        finally { saveReserve.disabled = false; }
    };
    function showDownloads(value) {
        if (!value) return;
        proxyMode.value = value.preferences?.proxy_mode || 'auto';
        modeHelp.textContent = proxyMode.value === 'proxy' ? t('Use the current proxy. No direct fallback.', '沿用当前代理，不尝试直连。') : proxyMode.value === 'direct' ? t('Download directly, ignoring proxies.', '忽略代理，直接下载。') : t('Compare proxy and direct connections automatically.', '自动测速，择优使用代理或直连。');
        authStatus.textContent = busy ? t('Stop setup to change the token, then resume with retained files.', '先停止安装，再修改 Token 并继续；已下载文件会保留。') :
            value.hf_token_set ? t('Session token set. Existing environment credentials apply when this field is cleared.', '已设置本次 Token；清除后仍可使用环境中已有的凭据。') :
            t('No session token set. Existing environment credentials still apply.', '未填写本次 Token；仍可使用环境中已有的凭据。');
        source.value = value.preferences?.source || 'auto';
        probeBusy = value.probe?.status === 'running'; probeButton.disabled = probeBusy;
        probeButton.textContent = probeBusy ? t('Testing…','正在测速…') : t('Test source speeds','重新测速');
        const progress = value.probe?.progress;
        sourceStatus.textContent = value.probe?.error || (probeBusy ? t('Testing sources… ','正在测速… ') + `${progress?.done || 0}/${progress?.total || '…'}` : '');
        const measured = value.probe?.status === 'complete' ? value.probe : value.preferences?.probe;
        if (measured?.network_unavailable && !probeBusy) sourceStatus.textContent = t('No sources reachable. Check your network or connection mode.', '下载源均无法连接，请检查网络或切换连接模式。');
        speeds.replaceChildren();
        if (!measured?.sources) return;
        const table = node('table'); table.className = 'fv-speed';
        const header = node('tr');
        for (const title of [t('Files','文件'),t('Source / route','来源／连接'),t('HTTP transfer','HTTP 传输速度')]) header.append(node('th',title));
        table.append(header);
        for (const [family, rows] of Object.entries(measured.sources)) for (const row of rows) {
            const tr = node('tr');
            const names = {'edge-models':t('Video model','视频模型'),'vdn-models':t('Decoder','解码器'),models:t('Text encoder','文本编码器'),pypi:t('Python packages','Python 依赖'),github:t('Tools','安装工具'),git:'Git',cuda:'CUDA'};
            if (family.startsWith('torch-')) names[family] = t('GPU packages','GPU 依赖');
            const rate = row.bytes_per_second / 2**20;
            const speed = !row.ok ? t('Unavailable','未连通') : row.speed_measured === false || !Number.isFinite(rate) || rate <= 0 ?
                t('Connected; insufficient sample','已连通，样本不足') : rate < .005 ? '<0.01 MiB/s' : `${rate.toFixed(rate < 1 ? 2 : 1)} MiB/s`;
            for (const text of [names[family] || family, `${row.id} · ${row.route === 'direct' ? t('direct','直连') : t('current connection','当前连接')}`,speed]) tr.append(node('td',text));
            if (!row.ok) tr.lastChild.className = 'fv-off';
            table.append(tr);
        }
        speeds.append(table,note(t('HTTP transfer speed, excluding connection wait. Parallel/Xet downloads may differ.','HTTP 传输测速已扣除连接等待；多线程／Xet 下载速度可能不同。')));
    }
    source.onchange = async () => {
        source.disabled = true;
        try { showDownloads(await request('network-source',{root:root.value,source:source.value})); sourceStatus.textContent=t('Switch requested for active downloads; retained fragments will be reused where compatible.','已请求立即切换当前下载，兼容的断点和分片将继续复用。'); }
        catch(error) { fail(error); }
        finally { source.disabled = false; }
    };
    proxyMode.onchange = async () => {
        proxyMode.disabled = true;
        try { showDownloads(await request('network-source', {root:root.value, proxy_mode:proxyMode.value})); }
        catch (error) { fail(error); }
        finally { proxyMode.disabled = false; }
    };
    probeButton.onclick = async () => {
        probeButton.disabled=true;
        try { showDownloads(await request('network-probe',{root:root.value})); poll(); }
        catch(error) { probeButton.disabled=false; fail(error); }
    };
    const controls = () => {
        for (const e of [root, extra, copy, samplingCaches, inspect, use, hfToken, applyToken, clearToken]) e.disabled = busy;
        cancel.disabled = !busy; install.disabled = busy || !planId || !accept.checked; accept.disabled = busy; inspect.classList.toggle("fv-primary", !planId); install.classList.toggle("fv-primary", Boolean(planId));
        const why = busy || (ready && !planId) ? "" : planStale ? t("Settings changed. Click Detect & review again to update the plan.", "设置已更改，请重新点击「检测并预览」更新安装计划。")
            : planErrors ? t("The plan has problems shown in red above. Please resolve them, then click Detect & review again.", "安装计划中有标红的问题，请解决后重新点击「检测并预览」。")
            : !planId ? t("Click Detect & review first. Install / repair becomes available once the plan is ready.", "请先点击「检测并预览」，生成安装计划后即可安装。")
            : !accept.checked ? t("Tick the agreement above to enable Install / repair.", "勾选上方的同意选项后，即可点击「安装／修复」。") : "";
        blocked.textContent = why; blocked.hidden = !why;
    };
    // A changed setting makes a shown plan outdated: clear it rather than leave it beside a disabled button.
    const invalidate = (stale = false) => {
        if (stale && lastPlanId && !busy) { planStale = true; planErrors = false; plan.replaceChildren(); if (!state.classList.contains("fv-failure")) state.textContent = ""; }
        planId = undefined; accept.checked = false; acceptLabel.hidden = true; controls();
    };
    async function setToken(value) {
        applyToken.disabled = clearToken.disabled = true;
        try {
            await request('network-token', {hf_token:value}); hfToken.value = '';
            invalidate(true); await poll();
        } catch(error) { fail(error); }
        finally { controls(); }
    }
    applyToken.onclick = () => setToken(hfToken.value);
    clearToken.onclick = () => setToken('');
    for (const e of [root, extra, copy, samplingCaches]) e.oninput = () => invalidate(true);
    accept.onchange = controls;
    function showPlan(value) {
        plan.replaceChildren();
        const gpu = value.inventory?.selected_gpu || {}, local = value.local_models || {};
        const facts = [
            [t("GPU", "显卡"), gpu.name || "—"],
            [t("Model download", "需下载模型"), gib(value.model_download_bytes)],
            [t("Models already present", "安装目录内已有模型"), gib(value.existing_model_bytes_size_matched || 0)],
            [t("Local models reused", "复用本地模型"), gib(local.reused_bytes || 0)],
            [t("Hardlinked / copied", "硬链接／复制"), `${gib(local.linked_bytes || 0)} / ${gib(local.copy_bytes || 0)}`],
            [t("Additional disk estimate", "额外磁盘预估"), gib(value.disks?.reduce((sum, d) => sum + d.needed_bytes, 0))],
        ];
        if (value.prepared_model) facts.push([t("Model", "模型"), `${value.prepared_model.repo} · ${value.prepared_model.scale_granularity === "int8_convrot"
            ? t("slim ConvRot int8, no local conversion", "精简 ConvRot int8，无需本地转换")
            : t("slim FP8, no local conversion", "精简 FP8，无需本地转换")}`]);
        const list = node('dl'); list.className = 'fv-facts';
        for (const [key, shown] of facts) list.append(node('dt', key), node('dd', shown));
        plan.append(list);
        if (value.prepared_model?.private) plan.append(note(t("Private repository: authorized Hugging Face login/token required.", "私有仓库：需要有访问权限的 Hugging Face 登录／token。")));
        const licenses = node("details"); licenses.append(node("summary", t("Model and toolkit licenses", "模型与工具包许可证")), node("pre", value.licenses?.join("\n") || "")); plan.append(licenses);
        if (value.errors?.length) { const e = node("p", value.errors.join("\n")); e.className = "fv-failure"; plan.append(e); }
    }
    function render(task) {
        busy = Boolean(task.busy); ready = Boolean(task.ready) || ready && task.status !== 'failed';
        inputs.hidden = busy; plan.hidden = busy;
        if (task.plan_id && task.plan_id !== lastPlanId) {
            lastPlanId = task.plan_id; planErrors = Boolean(task.plan?.errors?.length); planStale = false;
            planId = planErrors ? undefined : task.plan_id;
            accept.checked = false; acceptLabel.hidden = !planId; showPlan(task.plan);
            if (planErrors) plan.querySelector('.fv-failure')?.scrollIntoView({block: 'nearest'});
        }
        acceptLabel.hidden = busy || !planId;
        const p = task.progress || {};
        state.className = task.status === "failed" ? "fv-failure" : task.ready ? "fv-ready" : "";
        state.textContent = task.error || (task.ready ? t("Ready. Generate with your existing workflow.", "准备就绪，可使用现有工作流生成。") : busy ? p.label || t("Checking configuration and local models…", "正在检查配置和本地模型…") : task.status === "complete" && task.action === "plan" ? (planId ? t("Review the plan, then install.", "请确认计划后安装。") : "") : task.status === "cancelled" ? t("Stopped. Files retained for resuming.", "已停止，文件保留，可继续安装。") : "");
        const phases = task.phase_progress || {}, valid = Number.isFinite(p.total) && p.total > 0 && Number.isFinite(p.done) && p.done >= 0 && p.done <= p.total;
        overall.hidden = !busy;
        overallBar.max = phases.total > 0 ? phases.total : 1; overallBar.value = phases.done || 0;
        overallText.textContent = phases.total > 0 ? `${t("Overall", "整体进度")} · ${phases.done} / ${phases.total} ${t("stages complete", "阶段已完成")} · ${Math.floor(100 * phases.done / phases.total)}%` : t("Preparing installation plan…", "正在准备安装计划…");
        bar.hidden = !busy; bar.max = valid ? p.total : 1; bar.value = valid ? p.done : 0;
        const amount = n => p.total >= 2 ** 30 ? gib(n) : `${(n / 2 ** 20).toFixed(1)} MiB`;
        const count = valid ? `${Math.floor(100 * p.done / p.total)}% · ${p.unit === "bytes" ? `${amount(p.done)} / ${amount(p.total)}` : `${p.done} / ${p.total}`}` : t("Measuring progress…", "正在获取进度…");
        const eta = valid && Number.isFinite(p.remaining_seconds) && p.remaining_seconds > 0 ? `${p.unit === "bytes" ? t("File ETA", "本文件预计剩余") : t("ETA", "预计剩余")} ~${Math.ceil(p.remaining_seconds)} s` : t("Estimating remaining time…", "正在估算剩余时间…");
        counters.textContent = busy ? `${count} · ${eta}` : "";
        const others = (task.active_tasks || []).slice(1).map(p => p.label).filter(Boolean);
        detail.textContent = [p.detail, p.resource, others.length ? `${t("Also running", "同时进行")}: ${others.join(" / ")}` : ""].filter(Boolean).join(" · ");
        log.textContent = [task.log, task.tail].filter(Boolean).join("\n"); controls();
    }
    async function poll() {
        clearTimeout(pollTimer);
        try { const info = await request(); token = info.discovery.token; render(info.task); showDownloads(info.discovery.downloads); }
        catch (error) { fail(error); }
        if (dialog.open && (busy || probeBusy)) pollTimer = setTimeout(poll, 750);
    }
    inspect.onclick = async () => {
        invalidate(); busy = true; controls();
        try { render(await request("inspect", {root: root.value, extra_libraries: extra.value.split("\n").map(s => s.trim()).filter(Boolean), copy: copy.checked, sampling_caches: samplingCaches.checked})); poll(); }
        catch (error) { busy = false; controls(); fail(error); }
    };
    install.onclick = async () => {
        try { render(await request("install", {plan_id: planId, accept_licenses: accept.checked})); invalidate(); poll(); }
        catch (error) { fail(error); }
    };
    use.onclick = async () => {
        try { const result = await request("use", {root: root.value}); ready = Boolean(result.ready); state.textContent = result.ready ? t("Existing engine connected. Ready to generate.", "已连接现有引擎，可以生成。") : result.detail; state.className = "fv-ready"; invalidate(); }
        catch (error) { fail(error); }
    };
    cancel.onclick = async () => { try { render(await request("cancel")); poll(); } catch (error) { fail(error); } };
    dialog.onclose = () => { clearTimeout(pollTimer); for (const dispose of motions) dispose(); dialog.remove(); if (current === dialog) current = undefined; };
    try {
        const info = await request(); token = info.discovery.token; root.value = info.discovery.root;
        showDownloads(info.discovery.downloads);
        showResources(info.discovery.resources);
        samplingText.textContent = t('Prepare all quality levels (optional)', '提前下载全部质量档位（可选）')
            + (info.discovery.sampling_cache_bytes ? ' · ' + gib(info.discovery.sampling_cache_bytes) : '');
        // New installations prepare every quality level; a ready one adds them only when chosen.
        samplingCaches.checked = !info.discovery.ready;
        paths.textContent = info.discovery.libraries.join("\n") || t("No model libraries found; add a folder above.", "没有发现模型目录，可在上方添加。");
        ready = Boolean(info.discovery.ready);
        render(info.task);
        installation.open = !info.discovery.ready || busy;
        if (info.discovery.ready && !busy) state.textContent = t("Ready to generate.", "已就绪，可以开始生成。");
        if (busy || probeBusy) poll();
    } catch (error) { fail(error); }
}
