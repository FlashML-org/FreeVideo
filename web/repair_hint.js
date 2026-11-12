// Where to repair a damaged install depends on whether the launcher started this page.
// U+2060 keeps "环境" and "安装／修复" from breaking across lines in narrow dialogs.
let launcher = false;
let offlineInstall = false;

export const setLauncherPresent = present => { launcher = Boolean(present); };
export const setOfflineInstall = offline => { offlineInstall = offline === true; };

export const repairAction = () => offlineInstall
    ? ['Import the offline package again on the Installation page of the launcher, then retry.', '可在启动器“安\u2060装”页重新导入离线包后重试。']
    : launcher
    ? ['Select "Repair" in the launcher under Settings > Environment, then retry.', '可在启动器“设置 → 环⁠境”里点“修复”后重试。']
    : ['Select "Install / repair" in the page settings, then retry.', '可在页面设置中点“安⁠装⁠／⁠修⁠复”后重试。'];
