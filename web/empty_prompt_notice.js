// One notice for every generate button that is pressed with an empty prompt: the sentence and one button,
// right after the pressed control. Typing in the prompt box or pressing elsewhere removes it.
if (typeof document !== 'undefined') {
    for (const name of ['result_actions.css', 'empty_prompt_notice.css']) {
        const link = document.createElement('link');
        link.rel = 'stylesheet'; link.href = new URL('./' + name, import.meta.url).href; document.head.append(link);
    }
}

let shown = null;

export function dismissEmptyPrompt() { if (shown) shown.dismiss(); }

// `anchor`: the control (or its row) the notice follows. `field`: the prompt box, which takes the focus back.
// `onContinue` runs after the notice is gone.
export function showEmptyPrompt({t, anchor, field = null, onContinue}) {
    dismissEmptyPrompt();
    const doc = document;
    const box = doc.createElement('div'); box.className = 'fv-empty-prompt fv-actions'; box.setAttribute('role', 'status');
    const sentence = doc.createElement('span'); sentence.textContent = t('The prompt is empty.', '提示词为空。');
    const go = doc.createElement('button'); go.type = 'button'; go.className = 'fv-action fv-action-primary';
    const label = doc.createElement('span'); label.textContent = t('Generate anyway', '继续生成'); go.append(label);
    box.append(sentence, go);
    const gone = () => {
        doc.removeEventListener('pointerdown', outside, true);
        field?.removeEventListener('input', dismiss);
        box.remove(); if (shown?.box === box) shown = null;
    };
    const dismiss = () => gone();
    const outside = event => { if (!box.contains(event.target)) gone(); };
    go.onclick = () => { gone(); onContinue(); };
    anchor.after(box);
    doc.addEventListener('pointerdown', outside, true);
    field?.addEventListener('input', dismiss);
    field?.focus({preventScroll: true});
    shown = {box, dismiss};
    return box;
}
