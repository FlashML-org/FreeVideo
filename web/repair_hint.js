// Where to repair a damaged install depends on whether the launcher started this page.
// U+2060 keeps "环境", "检测并预览" and "安装／修复" from breaking across lines in narrow dialogs.
// On the page, Install / repair stays unavailable until Detect & review has run, so the hint names both.
let launcher = false;
let offlineInstall = false;

export const setLauncherPresent = present => { launcher = Boolean(present); };
export const setOfflineInstall = offline => { offlineInstall = offline === true; };

export const repairAction = () => offlineInstall
    ? ['Import the offline package again on the Installation page of the launcher, then retry.', '可在启动器“安\u2060装”页重新导入离线包后重试。']
    : launcher
    ? ['Select "Repair" in the launcher under Settings > Environment, then retry.', '可在启动器“设置 → 环⁠境”里点“修复”后重试。']
    : ['In the page settings, select "Detect & review", then "Install / repair", and retry.',
        '可在页面设置中先点“检⁠测⁠并⁠预⁠览”，再点“安⁠装⁠／⁠修⁠复”后重试。'];
