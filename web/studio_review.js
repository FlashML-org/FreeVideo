/**
 * Interactive review card for completed chain scenes in FreeVideo Studio.
 */
import { app } from '../../scripts/app.js';

/**
 * Mounts an interactive review card in the result panel when a scene finishes.
 *
 * @param {HTMLElement} container The result container.
 * @param {Object} chain The current chain object.
 * @param {Object} chainController The studio chain controller.
 * @param {Function} t Translation helper function.
 * @returns {HTMLElement|null} The mounted card element or null.
 */
export function mountReviewCard(container, chain, chainController, t = (en, zh) => en) {
    container.querySelectorAll('.fv-review-card').forEach(c => c.remove());
    if (chain?.state !== 'awaiting_review') return null;

    const block = chain.blocks?.filter(b => b.state === 'done' && b.output).at(-1);
    if (!block || !block.output) return null;

    const card = document.createElement('div');
    card.className = 'fv-review-card';
    card.style.cssText = `
        background: linear-gradient(145deg, rgba(30, 27, 75, 0.9), rgba(15, 23, 42, 0.95));
        border: 1px solid #6366f1;
        border-radius: 12px;
        padding: 14px;
        display: flex;
        flex-direction: column;
        gap: 10px;
        box-shadow: 0 8px 32px rgba(99, 102, 241, 0.25);
        margin: 10px 0;
    `;

    const header = document.createElement('div');
    header.style.cssText = 'display: flex; align-items: center; justify-content: space-between;';

    const title = document.createElement('div');
    title.style.cssText = 'font-size: 13px; font-weight: 700; color: #e0e7ff; display: flex; align-items: center; gap: 8px;';

    const pulse = document.createElement('span');
    pulse.style.cssText = 'width: 8px; height: 8px; border-radius: 50%; background: #a855f7; box-shadow: 0 0 8px #a855f7; display: inline-block;';

    const titleText = document.createElement('span');
    titleText.textContent = `${t('Review Scene', '审核分镜')} ${block.index + 1} / ${chain.blocks.length}`;

    title.append(pulse, titleText);
    header.append(title);
    card.append(header);

    const video = document.createElement('video');
    video.style.cssText = 'width: 100%; max-height: 220px; background: #000; border-radius: 6px; object-fit: contain;';
    video.controls = true;
    video.autoplay = true;
    video.loop = true;
    const split = block.output.lastIndexOf('/');
    video.src = '/view?' + new URLSearchParams({
        filename: block.output.slice(split + 1),
        subfolder: split < 0 ? '' : block.output.slice(0, split),
        type: 'output'
    });
    card.append(video);

    const actions = document.createElement('div');
    actions.style.cssText = 'display: flex; flex-direction: column; gap: 6px;';

    const approveBtn = document.createElement('button');
    approveBtn.className = 'fv-primary';
    approveBtn.style.cssText = 'background: #10b981; border-color: #059669; color: #fff; font-weight: 600; padding: 8px 12px; cursor: pointer;';
    approveBtn.textContent = `✓ ${t('Looks good — Continue to next scene', '画面满意 — 继续生成下一段')}`;
    approveBtn.onclick = async () => {
        approveBtn.disabled = true;
        approveBtn.textContent = t('Continuing…', '正在继续…');
        try {
            await chainController.approve();
            card.remove();
        } catch (err) {
            approveBtn.disabled = false;
            approveBtn.textContent = `✓ ${t('Looks good — Continue to next scene', '画面满意 — 继续生成下一段')}`;
            try { app.extensionManager?.toast?.add?.({ severity: 'error', summary: t('Approval failed', '确认失败'), detail: err.message, life: 6000 }); } catch {}
        }
    };

    const redoBtn = document.createElement('button');
    redoBtn.style.cssText = 'background: rgba(245, 158, 11, 0.2); border: 1px solid rgba(245, 158, 11, 0.4); color: #fcd34d; padding: 7px 12px; font-weight: 500; cursor: pointer;';
    redoBtn.textContent = `↻ ${t('Redo this scene (randomize seed)', '重试这一段 (换个种子)')}`;
    redoBtn.onclick = async () => {
        redoBtn.disabled = true;
        redoBtn.textContent = t('Regenerating…', '正在重新生成…');
        try {
            await chainController.redo();
            card.remove();
        } catch (err) {
            redoBtn.disabled = false;
            redoBtn.textContent = `↻ ${t('Redo this scene (randomize seed)', '重试这一段 (换个种子)')}`;
            try { app.extensionManager?.toast?.add?.({ severity: 'error', summary: t('Redo failed', '重试失败'), detail: err.message, life: 6000 }); } catch {}
        }
    };

    const stopBtn = document.createElement('button');
    stopBtn.className = 'fv-quiet';
    stopBtn.style.cssText = 'border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; padding: 6px 12px; cursor: pointer;';
    stopBtn.textContent = `⏹ ${t('Stop here & join scenes generated so far', '在此停止并接合已完成的分镜')}`;
    stopBtn.onclick = async () => {
        stopBtn.disabled = true;
        try {
            await chainController.cancel();
            card.remove();
        } catch (err) {
            stopBtn.disabled = false;
        }
    };

    actions.append(approveBtn, redoBtn, stopBtn);
    card.append(actions);

    container.prepend(card);
    return card;
}
