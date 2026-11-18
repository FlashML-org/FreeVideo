// Hover words for every control of the Autopilot view: a name, one line on what it does and what it gives, and
// the key. The shell's tooltip (tooltip.js) reads data-fv-tip-name / -use / -key, and -why on a disabled control.
// `title: true` also writes them as a title, for a surface the shell's tooltip does not cover.
export const TIPS = Object.freeze({
    newChat: {name: ['New chat', '新对话'], use: ['Start a new film from a blank chat', '从空白对话开始一部新片']},
    search: {name: ['Search chats', '搜索对话'], use: ['Find a chat by its name', '按名称找到一段对话']},
    chat: {name: ['Open chat', '打开对话'], use: ['Read this chat and keep talking', '查看这段对话并继续']},
    more: {name: ['More', '更多'], use: ['Rename or delete this chat', '重命名或删除这段对话']},
    collapseLeft: {name: ['Hide chats', '收起对话栏'], use: ['More room for the conversation', '给对话留出更多空间']},
    expandLeft: {name: ['Show chats', '展开对话栏'], use: ['See and switch your chats', '查看和切换对话']},
    collapseRight: {name: ['Hide tasks', '收起任务栏'], use: ['More room for the conversation', '给对话留出更多空间']},
    expandRight: {name: ['Show tasks', '展开任务栏'], use: ['See what is being made and its details', '查看正在生成的内容和详情']},
    chats: {name: ['Chats', '对话'], use: ['Open the chat list', '打开对话列表']},
    tasks: {name: ['Tasks', '任务'], use: ['What is being made, and the details', '正在生成的内容和详情']},
    connected: {name: ['Assistant connected', '助手已连接'], use: ['Disconnect it, or see how it is connected', '断开，或查看连接方式']},
    disconnected: {name: ['Not connected', '未连接'], use: ['See why, and connect again', '查看原因并重新连接']},
    disconnect: {name: ['Disconnect', '断开'], use: ['It stops hearing this chat until it calls again', '助手不再收到这里的对话，再联系时自动重连']},
    connectSteps: {name: ['Connection steps', '连接方式'], use: ['See how it is connected, or connect another', '查看连接方式，或再连一个']},
    reconnect: {name: ['Reconnect', '重新连接'], use: ['Copy the steps and paste them to your assistant', '复制连接说明，粘贴给助手']},
    goRunning: {name: ['Go to the task', '定位到任务'], use: ['Scroll the chat to what is being made', '把对话滚到正在生成的内容']},
    stopAll: {name: ['Stop all', '全部停止'], use: ['Stop everything the assistant is making', '停止助手正在生成的全部内容']},
    taskSearch: {name: ['Search tasks', '搜索任务'], use: ['Find a task by its prompt or name', '按提示词或名称找到任务']},
    task: {name: ['Task', '任务'], use: ['Show its details and find it in the chat', '查看详情并在对话里定位']},
    locate: {name: ['Show in chat', '在对话中定位'], use: ['Scroll the chat to this card', '把对话滚到这张卡片']},
    stop: {name: ['Stop', '停止'], use: ['Stop making this; nothing is kept', '停止生成这一条，不保留结果']},
    open: {name: ['Open', '打开'], use: ['Open it in your works', '在作品库里打开']},
    openPicture: {name: ['Open', '打开'], use: ['View the picture full size', '查看大图']},
    edit: {name: ['Edit', '剪辑'], use: ['Open it in Edit to cut and add sound', '在剪辑视图里继续剪辑和配音']},
    download: {name: ['Download', '下载'], use: ['Save the picture to this computer', '把图片保存到本机']},
    why: {name: ['See why', '查看原因'], use: ['What went wrong and what to do', '出了什么问题、可以怎么做']},
    retry: {name: ['Retry', '重试'], use: ['Try again with the same request', '用同样的要求再试一次']},
    continue: {name: ['Continue generating', '继续生成'], use: ['Go on from the finished parts', '从已完成的部分接着生成']},
    resend: {name: ['Send again', '再发一次'], use: ['Send the same words again', '把同样的话再发一次']},
    report: {name: ['Download report', '下载报告'], use: ['Save the diagnostic report to send us', '保存诊断报告，方便发给我们']},
    send: {name: ['Send', '发送'], use: ['Send this to the assistant', '把这句话发给助手'], key: 'Enter'},
    halt: {name: ['Stop', '停止'], use: ['Stop the reply that is being written', '停止助手正在写的回复']},
    attach: {name: ['Add pictures', '添加图片'], use: ['Give the assistant reference pictures', '给助手参考图片']},
    whitebox: {name: ['White model', '白模'], use: ['Preview the motion as a white model first', '先用白模预览动作']},
    scenes: {name: ['Ready scenes', '现成场景'], use: ['Start from a ready-made scene', '从现成的场景开始']},
    latest: {name: ['Latest', '最新'], use: ['Jump to the newest message', '跳到最新的消息']},
    play: {name: ['Play', '播放'], use: ['Play it here, with sound', '在这里播放，带声音'], key: 'Space'},
    editMessage: {name: ['Edit', '编辑'], use: ['Change your last message and send it again', '修改最后一句并重新发送']},
    start: {name: ['Start', '开始'], use: ['Film this plan now', '按这份计划开拍']},
    notNow: {name: ['Not now', '先不'], use: ['Leave this plan for now', '暂时不拍这份计划']},
    example: {name: ['Example', '示例'], use: ['Put it in the box to edit, then send', '放进输入框，改好再发送']},
    rename: {name: ['Rename', '重命名'], use: ['Give this chat a name you will find', '给这段对话起个好找的名字']},
    remove: {name: ['Delete', '删除'], use: ['Delete this chat; you confirm first', '删除这段对话，会先确认']},
    removeYes: {name: ['Delete', '删除'], use: ['Delete this chat for good', '确认删除，不能撤销']},
    removeNo: {name: ['Cancel', '取消'], use: ['Keep this chat', '保留这段对话']},
    stopDelete: {name: ['Stop and delete', '停止并删除'], use: ['Stop its work, then delete the chat', '先停止生成，再删除对话']},
    scene: {name: ['Ready scene', '现成场景'], use: ['Make a video from this scene', '用这个场景生成视频']},
    dropPicture: {name: ['Remove', '移除'], use: ['Take this picture out of the message', '从这条消息里拿掉这张图']},
    uploadAgain: {name: ['Upload again', '重新上传'], use: ['Try sending this picture again', '再上传一次这张图片']},
    seek: {name: ['Position', '播放位置'], use: ['Drag to move through the video', '拖动以跳到视频的任意位置']},
    more8: {name: ['More beats', '更多节拍'], use: ['Show the rest of the outline', '展开剩下的分镜']},
    whiteboxGo: {name: ['Generate video', '生成视频'], use: ['Make the video from this white model', '按这个白模生成视频']},
    whiteboxEdit: {name: ['Edit prompt', '改描述'], use: ['Put the description in the box to change', '把描述放进输入框修改']},
    whiteboxSkip: {name: ['Skip', '跳过'], use: ['Go on without this preview', '不用这个预览，继续']},
    editCancel: {name: ['Cancel', '取消'], use: ['Keep the message as it was', '保留原来的消息']},
    editSend: {name: ['Send', '发送'], use: ['Send the changed message', '发送修改后的消息'], key: 'Enter'},
    copyConnect: {name: ['Copy', '复制'], use: ['Copy the steps to paste to your assistant', '复制连接说明，粘贴给助手']},
    retryAddress: {name: ['Retry', '重试'], use: ['Ask for the connection steps again', '重新获取连接说明']},
    close: {name: ['Close', '关闭'], use: ['Back to the chat', '回到对话'], key: 'Esc'},
    activity: {name: ['Assistant tasks', '助手的任务'], use: ['See what the assistant is making', '查看助手正在生成的内容']},
    undoDelete: {name: ['Undo', '撤销'], use: ['Bring the deleted chat back', '找回刚删除的对话']},
    backToChats: {name: ['Back to chats', '返回对话'], use: ['Read your chats; connect later', '先看对话，稍后再连接']},
    newChatError: {name: ['New chat', '新对话'], use: ['Start a new chat; this one is full', '这段对话已满，开始新对话']},
    history: {name: ['History and templates', '历史与模板'], use: ['Reuse a sentence or start from a template', '用说过的话或模板开始']},
    historyRow: {name: ['Use again', '再用一次'], use: ['Put this sentence in the box', '把这句话放进输入框']},
    templateRow: {name: ['Template', '模板'], use: ['Put it in the box; fill the marked words', '放进输入框，替换标出的词']},
    filter: {name: ['Show', '筛选'], use: ['Show only tasks in this state', '只看这个状态的任务']},
    inspector: {name: ['Inspector', '素材检查器'], use: ['Fold or show the picked task\'s details', '收起或展开所选任务的详情']},
});

// Why a control cannot be used now, in one line with the way out; '' when it can.
export const WHY = Object.freeze({
    notConnected: ['Connect an assistant first', '先连接助手'],
    loading: ['This chat is still loading', '这段对话还在读取'],
    empty: ['Write a sentence or add a picture first', '先写一句话或加一张图片'],
    uploading: ['Wait for the pictures to finish uploading', '等图片上传完'],
    sending: ['This message is being sent', '这条消息正在发送'],
    sceneOnly: ['A ready scene takes words only', '现成场景只接受文字'],
    creating: ['A new chat is being made', '正在新建对话'],
    stopping: ['It is stopping', '正在停止'],
    notListening: ['Ask the assistant to continue in its own window', '请在助手的窗口里让它继续'],
});

// Sets the hover words on `node`; `why` (a reason a disabled control cannot be used) replaces the purpose line.
export function tip(node, key, t, {why = '', title = false} = {}) {
    const row = TIPS[key];
    if (!node || !row) return node;
    node.dataset.fvTipName = t(...row.name);
    node.dataset.fvTipUse = t(...row.use);
    if (row.key) node.dataset.fvTipKey = row.key; else delete node.dataset.fvTipKey;
    if (why) node.dataset.fvTipWhy = why; else delete node.dataset.fvTipWhy;
    // A title the control sets itself (a status, a full name) is kept.
    if (title && (!node.title || node.dataset.fvTipTitle === 'true')) {
        node.title = why ? `${t(...row.name)} — ${why}` : `${t(...row.name)} — ${t(...row.use)}${row.key ? ` (${row.key})` : ''}`;
        node.dataset.fvTipTitle = 'true';
    }
    return node;
}
