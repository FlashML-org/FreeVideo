// Where to repair a damaged install depends on whether the launcher started this page.
// U+2060 keeps "环境", "检测并预览" and "安装／修复" from breaking across lines in narrow dialogs.
// On the page, Install / repair stays unavailable until Detect & review has run, so the hint names both.
let launcher = false;
let offlineInstall = false;

export const setLauncherPresent = present => { launcher = Boolean(present); };
export const setOfflineInstall = offline => { offlineInstall = offline === true; };

const OFFLINE = ['Import the offline package again on the Installation page of the launcher, then retry.', '可在启动器“安\u2060装”页重新导入离线包后重试。'];
const LAUNCHER = ['Select "Repair" in the launcher under Settings > Environment, then retry.', '可在启动器“设置 → 环⁠境”里点“修复”后重试。'];
const PAGE = ['In the page settings, select "Detect & review", then "Install / repair", and retry.',
    '可在页面设置中先点“检⁠测⁠并⁠预⁠览”，再点“安⁠装⁠／⁠修⁠复”后重试。'];

export const repairAction = () => offlineInstall ? OFFLINE : launcher ? LAUNCHER : PAGE;

/** True when the shown text ends with the page-settings repair step (not the launcher's, nor an offline pack's). */
export const pageRepair = text => PAGE.some(step => String(text || '').includes(step));

/** Opens the page settings at Detect & review, through the same opener as the top-bar Settings. */
export const openRepairSettings = () => import('./setup.js').then(({openSetup}) => openSetup({section: 'repair'})).catch(() => {});

/**
 * The "Open settings" button shown beside a failure whose hint names the page settings. One component for the
 * failure card, the editor's failure rows and the dubbing note; `className` adds the place's own button classes.
 * `before` runs first on click (a failure dialog closes itself so the settings dialog is the one on top).
 */
export function repairButton(t, {className = '', before = null} = {}) {
    const button = document.createElement('button'); button.type = 'button';
    button.className = `fv-repair-open ${className}`.trim();
    button.textContent = t('Open settings', '打开设置');
    button.onclick = () => { before?.(); return openRepairSettings(); };
    return button;
}
