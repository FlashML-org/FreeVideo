import { api } from "../../scripts/api.js";
import { wordmark } from './wordmark.js';
import { openCompatibility } from './compatibility.js';
import { animateDetails, closeDialog } from './motion.js';
import { createGuideReplayEntry } from './first_run_guide.js';
import { issueButton } from './issue_button.js';
import { agentConfirmSettings } from './agent_confirm_settings.js';

const css = document.createElement('link');
css.rel = 'stylesheet'; css.href = new URL('./setup.css', import.meta.url).href;
document.head.append(css);

const languageOverride = typeof location !== 'undefined'
    ? new URLSearchParams(location.search).get('freevideo_lang') : null;
const cn = languageOverride === 'zh' || (languageOverride !== 'en'
    && String(navigator.language || '').toLowerCase().startsWith('zh'));
const t = (en, zh) => cn ? zh : en;
const INSTALLED_HINT = {en: 'Already installed. To reinstall, click Detect & review first.', zh: '已装好。要重装，请先点“检测并预览”。'};
const gib = n => Number.isFinite(n) ? (n / 2 ** 30).toFixed(1) + " GiB" : "—";
const SPEED_TEST_FAILED = 'The speed test did not finish. Click "Test source speeds".';
const reportable = value => !/The installation plan changed\. Detect configuration again|MODEL_MISMATCH_REPAIR:/.test(String(value || ''));
const failureText = value => {
    const text = String(value || ''), matches = [...text.matchAll(/MODEL_MISMATCH_REPAIR: ([^\r\n]+)/g)];
    if (!matches.length && text.includes('The installation plan changed. Detect configuration again')) {
        const files = text.includes('A file in the installation folder changed after the review');
        return files
            ? t('Files in the installation folder changed after you confirmed, so installation stopped. Click Detect & review, then Install / repair.',
                '确认之后安装位置里的文件有变化，已停止。请先点“检测并预览”，再点“安装／修复”。')
            : t('The settings, GPU or free disk space changed after you confirmed, so installation stopped. Click Detect & review, then Install / repair.',
                '确认之后设置、显卡或磁盘空间有变化，已停止。请先点“检测并预览”，再点“安装／修复”。');
    }
    if (text === SPEED_TEST_FAILED) return t('The speed test did not finish. Click "Test source speeds".', '测速没有完成，请点“重新测速”。');
    if (!matches.length) return text;
    const file = matches.at(-1)[1];
    return t(`${file} differs from the tested version and was left unchanged. Click Detect & review, then Install / repair. After you confirm, the original is renamed and kept, and the tested version is downloaded.`,
        `${file} 和测试过的版本不同，这次没有改动。请先点“检测并预览”，再点“安装／修复”，确认后原文件改名保留，再下载测试过的版本。`);
};
let current;

export async function openSetup() {
    if (current?.open) { current.focus(); return; }
    const dialog = document.createElement("dialog"); current = dialog;
    dialog.className = "fv-setup";
    const node = (tag, label) => { const e = document.createElement(tag); if (label) e.textContent = label; return e; };
    const note = label => { const e = node("p", label); e.className = "fv-muted"; return e; };
    const fault = (target, message) => {
        const text = failureText(message); target.textContent = text;
        if (text && reportable(message)) target.append(issueButton(t, () => ({where: ['Settings / installation', '设置／安装'], message: text})));
    };
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
    const extra = node("textarea"); extra.rows = 4; extraLabel.append(extra); inputs.append(extraLabel);
    const saveFolders = node('button', t('Save model folders', '保存模型目录'));
    const folderStatus = node('p'); folderStatus.setAttribute('role', 'status');
    const folderRow = node('div'); folderRow.className = 'fv-folder-save'; folderRow.append(saveFolders, folderStatus); inputs.append(folderRow);
    const libraries = node("details"); libraries.append(node("summary", t("Automatically detected model folders", "自动发现的模型目录")));
    const paths = node("pre"); libraries.append(paths); inputs.append(libraries);
    const copyLabel = node("label"); copyLabel.className = 'fv-check'; const copy = node("input"); copy.type = "checkbox";
    copyLabel.append(copy, node('span', t("Create independent copies (uses extra disk space)", "创建独立副本（额外占用磁盘）"))); inputs.append(copyLabel);
    const samplingLabel = node('label'); samplingLabel.className = 'fv-check';
    const samplingCaches = node('input'); samplingCaches.type = 'checkbox';
    const samplingText = node('span', t('Prepare all quality levels', '提前下载全部质量档位'));
    samplingLabel.append(samplingCaches, samplingText); inputs.append(samplingLabel);
    const downloads = node('section');
    downloads.append(node('h3', t('Downloads', '下载')));
    const modeLabel = node('label', t('Connection', '连接模式'));
    const proxyMode = node('select');
    for (const [id, name] of [['auto', t('Auto (recommended)', '自动（推荐）')], ['proxy', t('Proxy only', '仅代理')], ['direct', t('Direct only', '仅直连')]]) {
        const option = node('option', name); option.value = id; proxyMode.append(option);
    }
    modeLabel.append(proxyMode);
    downloads.append(modeLabel);
    const sourceLabel = node('label', t('Download source', '下载源'));
    const source = node('select');
    for (const [id, name] of [['auto',t('Automatic','自动选择')],['official','Hugging Face'],['hf-mirror','HF Mirror'],['modelscope','ModelScope']]) {
        const option = node('option',name); option.value = id; source.append(option);
    }
    const probeButton = node('button', t('Test source speeds', '重新测速'));
    const sourceRow = node('div'); sourceRow.className = 'fv-source-row'; sourceRow.append(source, probeButton);
    sourceLabel.append(sourceRow); downloads.append(sourceLabel);
    const sourceStatus = note(''), speeds = node('div');
    downloads.append(sourceStatus, speeds);
    const auth = node('details'); auth.className = 'fv-token';
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
    const reserveHelp = note(t('Extra VRAM to keep free. Applies from the next generation.', '额外保留的空闲显存，下次生成起生效。'));
    resources.append(autoLabel, reserveLabel, reserveHelp, saveReserve, reserveStatus);
    advanced.append(resources);
    const clearReserveStatus = () => { reserveStatus.textContent = ''; reserveStatus.className = 'fv-muted'; };
    reserveStatus.setAttribute('aria-live', 'polite');
    reserve.oninput = clearReserveStatus;
    reserveAuto.onchange = () => { reserve.disabled = reserveAuto.checked; clearReserveStatus(); };
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
        'Measures this computer and writes a report without changing settings. Each pass keeps the GPU busy for about half an hour, and generation cannot run meanwhile.',
        '测量这台电脑并生成报告，不改动设置。每轮约占用显卡半小时，期间不能生成。')),
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
    const use = node("button", t("Use existing", "使用已有安装"));
    const install = node("button", t("Install / repair", "安装／修复")); install.disabled = true;
    const cancel = node("button", t("Stop setup", "停止安装")); cancel.hidden = true;
    actions.append(inspect, use, install, cancel); installation.append(actions);
    // Why Install / repair is unavailable, so it never looks like a broken button.
    const blocked = note(""); blocked.classList.add("fv-setup-why"); blocked.setAttribute("aria-live", "polite"); blocked.hidden = true;
    installation.append(blocked);
    const guideReplay = createGuideReplayEntry({dialog, cn, api, openWorkspace: async () => {
        const {openCreativeWorkspace} = await import('./view_navigation.js');
        return openCreativeWorkspace();
    }});
    const agentConfirm = agentConfirmSettings(t, api);
    dialog.append(heading, installation, downloads, agentConfirm.element, advanced, guideReplay);
    const motions = [...dialog.querySelectorAll('details')].map(animateDetails);
    dialog.addEventListener('cancel', e => { e.preventDefault(); closeDialog(dialog); });
    document.body.append(dialog); dialog.showModal();
    let savedFolders = "", savedMissing = false;
    let token, planId, busy = false, probeBusy = false, pollTimer, lastPlanId, planErrors = false, planStale = false, ready = false, waitSince;
    // A click names what is happening at once, before the server has answered.
    const doing = (en, zh) => { waitSince ??= Date.now(); state.className = ''; state.textContent = t(en, zh); };
    const fail = error => { waitSince = undefined; installation.open = true; fault(state, error.message); state.className = "fv-failure"; state.scrollIntoView({block:'nearest'}); };
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
        // Mac generation shares unified memory and has no compatibility levels.
        compatibility.hidden = unified;
        reserveLabel.firstChild.textContent = reserveSummary.textContent;
        reserve.min = unified ? '1' : '0.2';
        reserveAuto.checked = value?.[reserveField] == null;
        reserve.disabled = reserveAuto.checked;
        reserve.value = reserveAuto.checked ? (unified ? '2.0' : '1.0') : String(value[reserveField]);
        reserveHelp.textContent = unified
            ? t('Extra memory to keep for the system and other apps. Applies from the next generation.', '额外保留给系统和其他程序的内存，下次生成起生效。')
            : t('Extra VRAM to keep free. Applies from the next generation.', '额外保留的空闲显存，下次生成起生效。');
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
            calibrateStatus.textContent = t('Running. Closing this panel keeps it running.',
                                            '正在运行，关闭面板不会中断。');
            installation.open = true;
            cancel.scrollIntoView({block: 'center'});
        } catch (error) {
            calibrateStatus.className = 'fv-failure';
            fault(calibrateStatus, error.message);
        } finally { calibrateButton.disabled = false; }
    };
    saveReserve.onclick = async () => {
        clearReserveStatus();
        if (!reserveAuto.checked && !reserve.value) {
            reserveStatus.className = 'fv-failure';
            reserveStatus.textContent = t('Enter a value.', '请输入数值。');
            reserve.focus();
            return;
        }
        if (!reserveAuto.checked && !reserve.checkValidity()) { reserve.reportValidity(); return; }
        saveReserve.disabled = true;
        try {
            showResources(await request('resources', {[reserveField]: reserveAuto.checked ? null : Number(reserve.value)}));
            reserveStatus.textContent = t('Saved · applies to the next request', '已保存 · 下次生成生效');
        } catch (error) { reserveStatus.className = 'fv-failure'; fault(reserveStatus, error.message); }
        finally { saveReserve.disabled = false; }
    };
    function showDownloads(value) {
        if (!value) return;
        proxyMode.value = value.preferences?.proxy_mode || 'auto';
        authStatus.textContent = busy ? t('Stop setup to change the token, then resume with retained files.', '先停止安装，再修改 Token 并继续；已下载文件会保留。') :
            value.hf_token_set ? t('Session token set. Existing environment credentials apply when this field is cleared.', '已设置本次 Token；清除后仍可使用环境中已有的凭据。') :
            t('No session token set. Existing environment credentials still apply.', '未填写本次 Token；仍可使用环境中已有的凭据。');
        source.value = value.preferences?.source || 'auto';
        probeBusy = value.probe?.status === 'running'; probeButton.disabled = probeBusy;
        probeButton.textContent = probeBusy ? t('Testing…','正在测速…') : t('Test source speeds','重新测速');
        const progress = value.probe?.progress;
        sourceStatus.textContent = failureText(value.probe?.error) || (probeBusy ? t('Testing sources… ','正在测速… ') + `${progress?.done || 0}/${progress?.total || '…'}` : '');
        const measured = value.probe?.status === 'complete' ? value.probe : value.preferences?.probe;
        if (measured?.network_unavailable && !probeBusy) sourceStatus.textContent = t('No sources reachable. Check your network or connection mode.', '下载源均无法连接，请检查网络或切换连接模式。');
        if (value.probe?.error || measured?.network_unavailable && !probeBusy) fault(sourceStatus, sourceStatus.textContent);
        speeds.replaceChildren();
        if (!measured?.sources) return;
        const table = node('table'); table.className = 'fv-speed';
        const header = node('tr');
        for (const title of [t('Files','文件'),t('Source / route','来源／连接'),t('HTTP transfer','HTTP 传输速度')]) header.append(node('th',title));
        table.append(header);
        for (const [family, rows] of Object.entries(measured.sources)) for (const row of rows) {
            const tr = node('tr');
            const names = {'edge-models':t('Video model','视频模型'),'vdn-models':t('Decoder','解码器'),'encoder-models':t('Text encoder','文本编码器'),models:t('Prompt model','提示词模型'),pypi:t('Python packages','Python 依赖'),github:t('Tools','安装工具'),git:'Git',cuda:'CUDA'};
            if (family.startsWith('torch-')) names[family] = t('GPU packages','GPU 依赖');
            const rate = row.bytes_per_second / 2**20;
            const speed = !row.ok ? t('Unavailable','未连通') : row.speed_measured === false || !Number.isFinite(rate) || rate <= 0 ?
                t('Connected; speed not measured yet','已连通，速度还没测出') : rate < .005 ? '<0.01 MiB/s' : `${rate.toFixed(rate < 1 ? 2 : 1)} MiB/s`;
            for (const text of [names[family] || family, `${row.id} · ${row.route === 'direct' ? t('Direct','直连') : t('Current connection','当前连接')}`,speed]) tr.append(node('td',text));
            if (!row.ok) tr.lastChild.className = 'fv-off';
            table.append(tr);
        }
        speeds.append(table,note(t('Speeds are a guide; actual downloads may differ.','测速只作参考，实际下载速度可能不同。')));
    }
    source.onchange = async () => {
        source.disabled = true;
        try { showDownloads(await request('network-source',{root:root.value,source:source.value})); sourceStatus.textContent=t('Switching sources; downloaded parts are kept where possible.','正在切换下载源，能续传的部分会保留。'); }
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
        for (const e of [root, extra, copy, samplingCaches, saveFolders, inspect, use, hfToken, applyToken, clearToken]) e.disabled = busy;
        cancel.hidden = !busy; install.disabled = busy || !planId || !accept.checked; accept.disabled = busy; inspect.classList.toggle("fv-primary", !planId); install.classList.toggle("fv-primary", Boolean(planId));
        const why = busy ? "" : planStale ? t("Settings changed. Click Detect & review again.", "设置已更改，请重新点“检测并预览”。")
            : planErrors ? t("Fix the problems marked in red above, then click Detect & review again.", "上面有标红的问题，处理后请重新点“检测并预览”。")
            : ready && !planId ? t(INSTALLED_HINT.en, INSTALLED_HINT.zh)
            : !planId ? ""
            : !accept.checked ? t("Tick the agreement above to enable Install / repair.", "勾选上方的同意选项后，可点击“安装／修复”。") : "";
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
    extra.oninput = () => { folderStatus.textContent = ""; folderStatus.className = ""; savedFolders = ""; savedMissing = false; invalidate(true); };
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
        for (const line of value.replace_models_text?.[cn ? 'zh' : 'en'] || []) {
            const replacement = node('div'); replacement.className = 'fv-model-repair';
            const path = note(line.path); path.classList.add('fv-model-repair-path');
            replacement.append(node('p', line.text), path); plan.append(replacement);
        }
        if (value.prepared_model?.private) plan.append(note(t("Private repository: authorized Hugging Face login/token required.", "私有仓库：需要有访问权限的 Hugging Face 登录／token。")));
        const licenses = node("details"); licenses.append(node("summary", t("Model and toolkit licenses", "模型与工具包许可证")), node("pre", value.licenses?.join("\n") || "")); plan.append(licenses);
        if (value.errors?.length) { const e = node("p", value.errors.map(failureText).join("\n")); e.className = "fv-failure"; plan.append(e); }
    }
    function render(task) {
        busy = Boolean(task.busy); if (!busy) waitSince = undefined; else waitSince ??= Date.now();
        ready = Boolean(task.ready) || ready && task.status !== 'failed';
        cancel.textContent = task.action === 'calibrate' ? t('Stop', '停止') : t('Stop setup', '停止安装');
        inputs.hidden = busy; plan.hidden = busy;
        if (task.plan_id && task.plan_id !== lastPlanId) {
            lastPlanId = task.plan_id; planErrors = Boolean(task.plan?.errors?.length); planStale = false;
            planId = planErrors ? undefined : task.plan_id;
            accept.checked = false; acceptLabel.hidden = !planId; showPlan(task.plan);
            if (planErrors) plan.querySelector('.fv-failure')?.scrollIntoView({block: 'nearest'});
        }
        acceptLabel.hidden = busy || !planId;
        const p = task.progress || {};
        state.className = task.status === "failed" ? "fv-failure" : task.ready && !savedMissing ? "fv-ready" : "";
        state.textContent = failureText(task.error) || (task.ready ? t("Ready to generate.", "准备就绪，可以开始生成。") : busy ? p.label || t("Checking configuration and local models…", "正在检查配置和本地模型…") : task.status === "complete" && task.action === "plan" ? (planId ? t("Review the plan, then install.", "请确认计划后安装。") : "") : task.status === "cancelled" ? t("Stopped. Files retained for resuming.", "已停止，文件保留，可继续安装。") : "");
        if (task.error) fault(state, task.error);
        const phases = task.phase_progress || {}, valid = Number.isFinite(p.total) && p.total > 0 && Number.isFinite(p.done) && p.done >= 0 && p.done <= p.total;
        overall.hidden = !busy;
        overallBar.max = phases.total > 0 ? phases.total : 1; overallBar.value = phases.done || 0;
        overallText.textContent = phases.total > 0 ? `${t("Overall", "整体进度")} · ${phases.done} / ${phases.total} ${t("stages complete", "阶段已完成")} · ${Math.floor(100 * phases.done / phases.total)}%` : t("Preparing installation…", "正在准备安装…");
        bar.hidden = !busy; bar.max = valid ? p.total : 1; bar.value = valid ? p.done : 0;
        const amount = n => p.total >= 2 ** 30 ? gib(n) : `${(n / 2 ** 20).toFixed(1)} MiB`;
        const count = valid ? `${Math.floor(100 * p.done / p.total)}% · ${p.unit === "bytes" ? `${amount(p.done)} / ${amount(p.total)}` : `${p.done} / ${p.total}`}` : t("Preparing…", "正在准备…");
        const eta = valid && Number.isFinite(p.remaining_seconds) && p.remaining_seconds > 0 ? `${p.unit === "bytes" ? t("File time left", "本文件预计剩余") : t("Time left", "预计剩余")} ~${Math.ceil(p.remaining_seconds)} s` : valid ? t("Estimating remaining time…", "正在估算剩余时间…") : `${t("Elapsed", "已用时")} ${Math.floor((Date.now() - (waitSince || Date.now())) / 1000)} s`;
        counters.textContent = busy ? `${count} · ${eta}` : "";
        const others = (task.active_tasks || []).slice(1).map(p => p.label).filter(Boolean);
        const progressDetail = (p.detail || '').replace(/^Keeping the original file: /, t('Keeping the original file: ', '正在保留原文件：'));
        detail.textContent = [progressDetail, p.resource, others.length ? `${t("Also running", "同时进行")}: ${others.join(" / ")}` : ""].filter(Boolean).join(" · ");
        log.textContent = [task.log, task.tail].filter(Boolean).join("\n"); controls();
    }
    async function poll() {
        clearTimeout(pollTimer);
        try { const info = await request(); token = info.discovery.token; render(info.task); showDownloads(info.discovery.downloads); }
        catch (error) { fail(error); }
        if (dialog.open && (busy || probeBusy)) pollTimer = setTimeout(poll, 750);
    }
    async function saveModelFolders() {
        const result = await request('model-directories', {extra_libraries: extra.value.split("\n")});
        extra.value = result.extra_libraries.join("\n");
        const missing = result.missing_libraries || [];
        savedMissing = missing.length > 0;
        savedFolders = !missing.length ? t('Saved.', '已保存。')
            : missing.length === 1 ? t('Saved. This folder was not found:', '已保存。找不到这个目录：')
            : t(`Saved. ${missing.length} folders were not found:`, `已保存。有 ${missing.length} 个目录找不到：`);
        folderStatus.className = savedMissing ? 'fv-warn' : 'fv-ready';
        folderStatus.replaceChildren(savedFolders);
        if (savedMissing) {
            // The full path is in the box above; show its start and last folder on one line.
            const first = missing[0], path = node('span', first.length > 44 ? `${first.slice(0, 14)}…${first.slice(-26)}` : first);
            path.className = 'fv-folder-path'; path.title = first; folderStatus.append(path);
        }
        return result;
    }
    saveFolders.onclick = async () => {
        invalidate(true); busy = true; controls();
        const before = [[...state.childNodes], state.className]; doing('Saving model folders…', '正在保存模型目录…');
        try { await saveModelFolders(); }
        catch (error) { savedFolders = ''; savedMissing = false; fault(folderStatus, error.message); folderStatus.className = 'fv-failure'; }
        finally { busy = false; waitSince = undefined; state.replaceChildren(...before[0]); state.className = before[1]; controls(); }
    };
    inspect.onclick = async () => {
        invalidate(); busy = true; controls(); doing('Checking installation…', '正在检查安装…');
        try { await saveModelFolders(); render(await request("inspect", {root: root.value, extra_libraries: extra.value.split("\n").map(s => s.trim()).filter(Boolean), copy: copy.checked, sampling_caches: samplingCaches.checked})); poll(); }
        catch (error) { busy = false; waitSince = undefined; controls(); fail(error); }
    };
    install.onclick = async () => {
        doing('Starting installation…', '正在开始安装…');
        try { render(await request("install", {plan_id: planId, accept_licenses: accept.checked})); invalidate(); poll(); }
        catch (error) { fail(error); }
    };
    use.onclick = async () => {
        try { const result = await request("use", {root: root.value}); ready = Boolean(result.ready); state.textContent = result.ready ? t("Existing installation connected. Ready to generate.", "已连接现有安装，可以生成。") : result.detail; state.className = "fv-ready"; invalidate(); }
        catch (error) { fail(error); }
    };
    cancel.onclick = async () => { try { render(await request("cancel")); poll(); } catch (error) { fail(error); } };
    dialog.onclose = () => { clearTimeout(pollTimer); for (const dispose of motions) dispose(); dialog.remove(); if (current === dialog) current = undefined; };
    doing('Reading installation settings…', '正在读取安装设置…');
    try {
        const info = await request(); token = info.discovery.token; root.value = info.discovery.root;
        extra.value = (info.discovery.extra_libraries || []).join("\n");
        showDownloads(info.discovery.downloads);
        showResources(info.discovery.resources);
        samplingText.textContent = t('Prepare all quality levels', '提前下载全部质量档位')
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
