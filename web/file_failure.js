// The cause of a file failure the engine reported (library delete, library listing, sharing export) as a sentence.
export function fileFailureText(reason, t) {
    return {
        in_use: t('The file is open in another program. Close that program, then try again.', '文件正被其他程序占用，请关闭该程序后重试。'),
        not_writable: t('FreeVideo has no permission to change the output folder. Check the folder, then try again.', 'FreeVideo 无权修改输出文件夹，请检查该文件夹的权限后重试。'),
        not_readable: t('The output folder cannot be read. Check that its disk is connected, then try again.', '无法读取输出文件夹，请确认磁盘已连接后重试。'),
        disk_full: t('The disk is full. Free some space, then try again.', '磁盘空间已满，请释放空间后重试。'),
    }[reason] || '';
}
// The reason a failed response carries, as text (sharing) or as JSON {reason} (library).
export async function responseFileReason(response) {
    try {
        const text = (await response.text()).trim();
        if (text.startsWith('{')) return JSON.parse(text).reason || '';
        return text;
    } catch { return ''; }
}
