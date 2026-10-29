// The list is a server snapshot, in the server's order. An unsaid current conversation need not be in it.
export const emptyConversations = () => ({supported: null, current: null, conversations: [], unreadable: 0});
export const selectConversation = (state, session) => ({...state, current: session ?? null});
export const hasConversations = state => state.supported === true && state.conversations.length > 0;
// The first other conversation with work under way; null when there is none.
export const runningElsewhere = (state, session) => state.conversations.find(row => row.running > 0 && row.id !== session) || null;
export const canDeleteConversation = row => !!row?.id && !(row.running > 0);
// A chat the user has not spoken in yet has no name of its own to change.
export const canRenameConversation = row => !!row?.id && !row.temporary;
// The one menu moves from closed to Delete, then to Delete/Cancel. Snapshots can close either stage.
export function conversationMenu(state, menu, action = 'reconcile', session = null) {
    const eligible = id => state.supported === true && state.conversations.some(row => row.id === id && (canDeleteConversation(row) || canRenameConversation(row)));
    if (action === 'close') return null;
    if (action === 'open') return eligible(session) ? {session, stage: 'delete'} : null;
    if (!menu || !eligible(menu.session)) return null;
    // Once asked to confirm a deletion, a chat that has started work is no longer one that can be deleted.
    if (menu.stage === 'confirm' && !state.conversations.some(row => row.id === menu.session && canDeleteConversation(row))) return null;
    if (action === 'confirm' && menu.stage === 'delete') return {...menu, stage: 'confirm'};
    return menu;
}
// Only deleting the conversation being read changes the reader's destination. An unsaid `here` is valid too.
export const removeConversation = (state, session, here = null) => ({...state,
    current: state.current === session ? here ?? null : state.current,
    conversations: state.conversations.filter(row => row.id !== session)});

export function mergeConversations(state, data, status = 200) {
    // A legacy server is probed once per page; ordinary errors retain the last good snapshot.
    if (status === 404) return {...state, supported: false, conversations: [], unreadable: 0};
    if (state.supported === false || status !== 200 || !Array.isArray(data?.conversations)) return state;
    const seen = new Set(), conversations = [];
    for (const row of data.conversations) {
        if (typeof row?.id !== 'string' || !row.id || typeof row.title !== 'string' || !row.title.trim() || seen.has(row.id)) continue;
        seen.add(row.id);
        const cover = typeof row.cover === 'string' && row.cover.startsWith('/') && !row.cover.startsWith('//')
            && !/[\\\s\u0000-\u001f\u007f]/.test(row.cover) ? row.cover : null;
        conversations.push({id: row.id, title: row.title, updated_at: row.updated_at,
            cover,
            running: Math.max(0, Math.floor(Number(row.running) || 0))});
        if (conversations.length === 50) break;
    }
    return {...state, supported: true, conversations,
        unreadable: Number.isSafeInteger(data.unreadable) && data.unreadable > 0 ? data.unreadable : 0};
}
