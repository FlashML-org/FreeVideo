// Where to repair a damaged install depends on whether the launcher started this page.
// A quoted button name stays on one line where the page shows it (i18n.js holdNames); these strings are plain.
// In Settings, Install stays unavailable until Check installation has run, so the hint names both.
import { plainText } from './i18n.js';

let launcher = false;
let offlineInstall = false;

export const setLauncherPresent = present => { launcher = Boolean(present); };
export const setOfflineInstall = offline => { offlineInstall = offline === true; };

const OFFLINE = ['Import the offline package again on the Installation page of the launcher, then retry.', '可在启动器“安装”页重新导入离线包后重试。'];
const LAUNCHER = ['Select "Repair" in the launcher under Settings > Environment, then retry.', '可在启动器“设置 → 环境”里点“修复”后重试。'];
const PAGE = ['In Settings, under Models, select "Check installation", then "Install", and retry.',
    '可在设置的“模型”页先点“检查安装”，再点“安装”后重试。'];
// The wording before Settings became a room, still recognised in texts made by older code paths.
const PAGE_BEFORE = ['In the page settings, select "Detect & review", then "Install / repair", and retry.',
    '可在页面设置中先点“检测并预览”，再点“安装／修复”后重试。'];

export const repairAction = () => offlineInstall ? OFFLINE : launcher ? LAUNCHER : PAGE;

/** True when the shown text ends with the page-settings repair step (not the launcher's, nor an offline pack's).
 * The shown text holds quoted names with word joiners; it is compared as plain text. */
export const pageRepair = text => [...PAGE, ...PAGE_BEFORE].some(step => plainText(text).includes(step));

/** Opens Settings at Models with Check installation in focus, through the same opener as the top-bar Settings. */
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
