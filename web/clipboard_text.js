// Writes text to the clipboard; resolves false when neither the clipboard API nor the old copy command is allowed.
// navigator.clipboard exists only in a secure context: a page opened from another device by its LAN address over http
// has none, so the selection copy below is what works there (#1097).
export async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through to the selection copy */ }
    const before = document.activeElement;
    const area = document.createElement('textarea'); area.value = text; area.setAttribute('readonly', '');
    area.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.append(area); area.select();
    try { return document.execCommand('copy'); } catch { return false; } finally {
        area.remove();
        // The keyboard goes back where it was (the button or menu that asked), not to the page body.
        if (before && before !== document.body && before.isConnected) before.focus?.({preventScroll: true});
    }
}
