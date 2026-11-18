const reduceMotion = () => document.documentElement.dataset.glass === 'solid' || document.documentElement.classList.contains('lite') || matchMedia('(prefers-reduced-motion: reduce)').matches;

// A view (full-window dialog) fades out over the view beneath it, opacity only, so the shared top bar stays still;
// smaller dialogs also sink a little. Inside a view switch the shell's cross-fade has already taken the picture:
// the dialog closes at once.
export function closeDialog(dialog) {
    if (!dialog.open || dialog.dataset.closing === 'true') return;
    // Escape does not close a whole view (the shell marks the view's last Escape); its tabs move between views.
    if (dialog.dataset.fvSurface === 'view' && performance.now() - (dialog.fvEscapedAt ?? -1e9) < 80 && !globalThis.FreeVideoShell?.switching?.()) return;
    if (globalThis.FreeVideoShell?.switching?.()) {
        // The browser delivers a dialog's close event with the next frame, and frames are held while a switch takes
        // its pictures: the view's close handler (which opens the next view) runs now instead, once.
        settleDialog(dialog);
        dialog.close();
        const handler = dialog.onclose;
        if (typeof handler === 'function') {
            dialog.onclose = null;
            // A view kept alive closes again later: its handler comes back once the browser's own close event is past.
            dialog.addEventListener('close', () => { if (dialog.onclose === null) dialog.onclose = handler; }, {once: true});
            try { handler.call(dialog, new Event('close')); } catch (error) { console.error('FreeVideo view close:', error); }
        }
        return;
    }
    settleDialog(dialog);
    if (reduceMotion() || !dialog.animate) { dialog.close(); return; }
    dialog.dataset.closing = 'true';
    const view = dialog.dataset.fvSurface === 'view';
    const animation = dialog.animate(view ? [{opacity: 1}, {opacity: 0}] : [{opacity: 1, transform: 'translateY(0) scale(1)'}, {opacity: 0, transform: 'translateY(6px) scale(.99)'}],
        {duration: view ? 200 : 160, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'forwards', id: 'fv-close'});
    // Closed first, then the fade's end state is dropped: nothing of it stays on a dialog that opens again (views and
    // panels kept alive reopen the same element), and the next close is not mistaken for one still running.
    animation.finished.then(() => { dialog.close(); settleDialog(dialog); }, () => { delete dialog.dataset.closing; });
}

// Drops whatever a close left on a dialog (the fade's end state, the closing mark); call before showing it again.
export function settleDialog(dialog) {
    for (const animation of dialog.getAnimations?.() || []) if (animation.id === 'fv-close') animation.cancel();
    delete dialog.dataset.closing;
}

// Keep native details/summary semantics, including keyboard clicks, while
// animating their measured height in both directions and on rapid toggles.
export function animateDetails(details) {
    const summary = details.querySelector('summary');
    let animation = null, expanded = details.open;
    const click = event => {
        if (reduceMotion() || !details.animate) return;
        event.preventDefault();
        const start = details.getBoundingClientRect().height;
        expanded = animation ? !expanded : !details.open;
        animation?.cancel();
        details.open = expanded;
        const end = details.getBoundingClientRect().height;
        details.open = true; details.dataset.expanded = String(expanded);
        details.style.overflow = 'hidden';
        const current = details.animate([{height: `${start}px`}, {height: `${end}px`}],
            {duration: 240, easing: 'cubic-bezier(.2,.7,.2,1)', fill: 'both'});
        animation = current;
        current.finished.then(() => {
            if (animation !== current) return;
            details.open = expanded; current.cancel(); animation = null;
            details.style.overflow = ''; delete details.dataset.expanded;
        }, () => {});
    };
    summary.addEventListener('click', click);
    return () => { summary.removeEventListener('click', click); animation?.cancel(); };
}
