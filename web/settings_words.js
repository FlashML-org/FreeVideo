// Settings' words and rules without the page: the language, sizes, what each installer refusal means and what to do
// next, and the pages with the old names callers still pass. No DOM, so tests/settings_words.mjs runs them in Node.
const languageOverride = typeof location !== 'undefined'
    ? new URLSearchParams(location.search).get('freevideo_lang') : null;
export const cn = languageOverride === 'zh' || (languageOverride !== 'en'
    && String(globalThis.navigator?.language || '').toLowerCase().startsWith('zh'));
export const t = (en, zh) => cn ? zh : en;
export const INSTALLED_HINT = {en: 'Already installed. To reinstall, click Check installation first.', zh: '已装好。要重装，请先点“检查安装”。'};
export const gib = n => Number.isFinite(n) ? (n / 2 ** 30).toFixed(1) + " GiB" : "—";
// Plan sizes pick their unit, so a few-MB repair does not read as "0.0 GiB".
// The cut-offs sit where rounding would print 1024.0 MiB or 1024 KiB, so those carry to the next unit.
export const bytesText = n => !(n > 0 && n < 1023.95 * 2 ** 20) ? gib(n)
    : n >= 1023.5 * 2 ** 10 ? (n / 2 ** 20).toFixed(1) + " MiB" : Math.max(1, Math.round(n / 2 ** 10)) + " KiB";
const SPEED_TEST_FAILED = 'The speed test did not finish. Click "Test source speeds".';
const BUSY_LOCK = /using this installation\/GPU lock/;
const BUSY_TEXT = {en: 'FreeVideo is busy with another task. Try again when it finishes. If nothing else is running, close duplicate FreeVideo instances, then try again.',
    zh: 'FreeVideo 正在处理另一个任务。请在它完成后重试。若没有其他任务在进行，请关闭重复运行的 FreeVideo 后重试。'};
// The speed-test row passes text that failureText already turned into the busy message.
export const isBusy = value => BUSY_LOCK.test(value) || value.startsWith(BUSY_TEXT.en) || value.startsWith(BUSY_TEXT.zh);
// Setup refusals whose cause and next step are known. Repair goes where the server's sentence pointed: the launcher when it watches.
const repairStep = text => text.includes('In the FreeVideo launcher, open Settings and click Repair.')
    ? t('In the FreeVideo launcher, open Settings and click Repair.', '请在 FreeVideo 启动器的设置里点“修复”。')
    : t('Click Check installation, then Install.', '请先点“检查安装”，再点“安装”。');
// The reserve's lower limit, said the same way whether the panel or the service stopped the value.
export const atLeast = least => t(`Enter at least ${least} GiB, or turn on Automatic optimization.`, `请输入不小于 ${least} GiB 的数值，或打开“自动优化”。`);
export const WAIT_REFUSAL = /^Wait for (the installation check|the installation|a placement test|a setup task) to finish; its progress is in this panel\.$/;
const SETUP_ERRORS = [
    // Another task holds the installation lock; any "Lock file open:" lines name the holders.
    [BUSY_LOCK, text => [t(BUSY_TEXT.en, BUSY_TEXT.zh), ...text.split(/\r?\n/).filter(line => line.startsWith('Lock file open:')).slice(0, 6)].join('\n')],
    [/^Choose an engine installation folder$/, () => t('Choose a FreeVideo installation folder first.', '请先选择 FreeVideo 安装位置。')],
    [/^Use a dedicated FreeVideo folder, not the ComfyUI root or its parent$/,
        () => t('Choose a separate folder for FreeVideo, not the program folder or the folder that contains it.',
            '请给 FreeVideo 单独选一个文件夹，不要用程序所在的文件夹或它的上一级。')],
    [/^Finish or cancel queued ComfyUI requests before installing$/,
        () => t('Generations are running or queued. Install after they finish, or cancel them first.', '仍有生成正在进行或排队。请等待完成或先取消，再安装。')],
    [/^Finish or cancel queued ComfyUI requests before calibrating$/,
        () => t('Generations are running or queued. Start measuring after they finish, or cancel them first.', '仍有生成正在进行或排队。请等待完成或先取消，再开始性能测量。')],
    [/^Wait for setup to finish$/, () => t('Installation is still running. Try again after it finishes.', '安装仍在进行，请在完成后重试。')],
    // The measurement refuses to start beside another setup task and names it; that is a
    // wait, not a fault, so it is shown as plain text without the report button.
    [WAIT_REFUSAL,
        text => {
            const names = {'the installation check': ['the installation check', '安装检查'], 'the installation': ['the installation', '安装'], 'a placement test': ['the performance measurement', '性能测量']};
            const [en, zh] = names[text.match(/^Wait for (.+) to finish/)[1]] ?? ['the setup task', '安装任务'];
            return t(`Wait for ${en} to finish; its progress is under Models.`, `请等待${zh}完成，进度见“模型”。`);
        }],
    [/^Inspect this installation again before installing$/, () => t('Click Check installation again, then Install.', '请重新点“检查安装”，再点“安装”。')],
    [/^Review the plan and accept its model\/toolkit licenses first$/, () => t('Tick the agreement to enable Install.', '勾选同意后，可点击“安装”。')],
    // Saving the reserve: the panel's own limits normally stop these first.
    [/^(Reserved VRAM|Reserved unified RAM) must be automatic or a finite number of at least ([\d.]+) GiB$/,
        text => atLeast(text.match(/at least ([\d.]+) GiB$/)[1])],
    [/^Could not save resource settings: /,
        () => t('The setting could not be saved. Check that the installation folder can be written to.', '设置未能保存，请检查安装位置是否可以写入。')],
    [/^FreeVideo setup is missing\./, text => t('This folder has no finished FreeVideo installation. ', '该文件夹中没有已安装的 FreeVideo。') + repairStep(text)],
    [/^FreeVideo setup is incomplete in /, text => t('The installation in this folder is not finished. ', '该文件夹中的安装尚未完成。') + repairStep(text)
        + t(' Existing files are reused.', '已有文件会继续使用。')],
    [/^FreeVideo device configuration is missing\./, text => t('This installation has not checked the GPU yet. ', '该安装尚未检查显卡。') + repairStep(text)],
    [/^The FreeVideo installation was moved\./, text => t('This installation was moved from another folder. ', '该安装是从其他文件夹移动过来的。') + repairStep(text)],
    [/^The FreeVideo Python environment is missing\./, text => t('This installation is missing its runtime. ', '该安装缺少运行环境。') + repairStep(text)],
];
export const setupError = text => SETUP_ERRORS.find(([pattern]) => pattern.test(text))?.[1](text);
export const reportable = value => !/The installation plan changed\. Detect configuration again|MODEL_MISMATCH_REPAIR:/.test(String(value || ''))
    && setupError(String(value || '')) === undefined && !isBusy(String(value || ''));
export const failureText = value => {
    const text = String(value || ''), matches = [...text.matchAll(/MODEL_MISMATCH_REPAIR: ([^\r\n]+)/g)];
    const known = setupError(text);
    if (known !== undefined) return known;
    if (!matches.length && text.includes('The installation plan changed. Detect configuration again')) {
        const files = text.includes('A file in the installation folder changed after the review');
        return files
            ? t('Files in the installation folder changed after you confirmed, so installation stopped. Click Check installation, then Install.',
                '确认之后安装位置里的文件有变化，已停止。请先点“检查安装”，再点“安装”。')
            : t('The settings, GPU or free disk space changed after you confirmed, so installation stopped. Click Check installation, then Install.',
                '确认之后设置、显卡或磁盘空间有变化，已停止。请先点“检查安装”，再点“安装”。');
    }
    if (text === SPEED_TEST_FAILED) return t('The speed test did not finish. Click "Test again".', '测速没有完成，请点“重新测速”。');
    if (!matches.length) return text;
    const file = matches.at(-1)[1];
    return t(`${file} differs from the tested version and was left unchanged. Click Check installation, then Install. After you confirm, the original is renamed and kept, and the tested version is downloaded.`,
        `${file} 和测试过的版本不同，这次没有改动。请先点“检查安装”，再点“安装”，确认后原文件改名保留，再下载测试过的版本。`);
};

// The pages, in their groups. `icon` names settings_kit's icons.
export const SECTIONS = [
    {group: ['Models & downloads', '模型与下载'], items: [
        {id: 'models', icon: 'cube', name: ['Models', '模型']},
        {id: 'downloads', icon: 'down', name: ['Downloads', '下载']},
        {id: 'lora', icon: 'layers', name: ['LoRA', 'LoRA']}]},
    {group: ['Use', '使用'], items: [
        {id: 'generation', icon: 'film', name: ['Generation', '生成']},
        {id: 'appearance', icon: 'palette', name: ['Appearance', '外观']},
        {id: 'assistant', icon: 'auto', name: ['Assistant', '助手']},
        {id: 'storage', icon: 'disk', name: ['Storage', '存储']}]},
    {group: ['Maintenance', '维护'], items: [
        {id: 'health', icon: 'shield', name: ['Page check', '页面检查']},
        {id: 'advanced', icon: 'wrench', name: ['Advanced', '高级']},
        {id: 'about', icon: 'update', name: ['Version & updates', '版本与更新']},
        {id: 'guide', icon: 'book', name: ['Getting started', '新手指引']}]},
];
// Old section names callers pass (failure cards, the preview's "change" link, the version entry).
const ALIASES = {repair: 'models', preview: 'generation', compatibility: 'advanced', version: 'about', updates: 'about', look: 'appearance'};
export const sectionOf = name => ALIASES[name] || (SECTIONS.some(g => g.items.some(i => i.id === name)) ? name : null);

