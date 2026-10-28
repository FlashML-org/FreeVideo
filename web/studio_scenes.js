/**
 * Multi-scene storyboard panel for FreeVideo Studio.
 */
export { mountReviewCard } from './studio_review.js';

export const SCENES_PROPERTY = 'freevideo_scenes';
export const MAX_SCENES = 5;

/** Convert seconds to legal continuation frames: 5 + 17n, minimum 39. */
export function secondsToLegalFrames(sec) {
    const target = Math.max(1.625, Number(sec) || 2);
    const n = Math.max(2, Math.round((target * 24 - 5) / 17));
    return 5 + 17 * n;
}

/** Convert frames back to approximate seconds. */
export function legalFramesToSeconds(frames) {
    return Math.round(((frames - 0.5) / 24) * 10) / 10;
}

export function createStudioScenes({ node, api, t = (en, zh) => en, onScenesChange = () => {} }) {
    node.properties ||= {};
    const defaultScenes = [
        { id: 's1', prompt: '', seconds: 5, seed: '' },
        { id: 's2', prompt: '', seconds: 5, seed: '' }
    ];
    let state = node.properties[SCENES_PROPERTY] || {
        enabled: false,
        askAfterEach: true,
        scenes: defaultScenes
    };

    function persist() {
        node.properties[SCENES_PROPERTY] = state;
        node.graph?.change();
        onScenesChange(state);
    }

    const container = document.createElement('details');
    container.className = 'fv-prompt-wizard fv-scenes-wizard';
    container.open = true;

    // Summary bar matching Director Checklist
    const head = document.createElement('summary');
    head.style.cssText = `
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        cursor: pointer;
        user-select: none;
        list-style: none;
    `;

    const left = document.createElement('span');
    left.style.cssText = 'display: inline-flex; align-items: center; gap: 8px; font-weight: 600; font-size: 13px; color: var(--fv-ink, #eceff4);';

    const titleLabel = document.createElement('span');
    titleLabel.textContent = `🎬 ${t('Multi-Scene Storyboard', '多分镜故事板')}`;

    const badge = document.createElement('span');
    badge.style.cssText = 'background: rgba(99, 102, 241, 0.2); border: 1px solid rgba(129, 140, 248, 0.4); color: #c7d2fe; padding: 1px 6px; border-radius: 10px; font-size: 11px; font-weight: 500;';
    badge.textContent = `${state.scenes.length} ${t('scenes', '段')}`;

    left.append(titleLabel, badge);

    // Quick "+ Add scene" button in summary
    const quickAddBtn = document.createElement('button');
    quickAddBtn.type = 'button';
    quickAddBtn.className = 'fv-quick-add-scene-btn';
    quickAddBtn.textContent = `+ ${t('Add scene', '添加分镜')}`;
    quickAddBtn.style.cssText = `
        background: #6366f1;
        border: 1px solid #818cf8;
        color: #ffffff;
        border-radius: 4px;
        padding: 2px 10px;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        line-height: 1.4;
    `;

    quickAddBtn.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        container.open = true;
        state.enabled = true;
        if (state.scenes.length < MAX_SCENES) {
            state.scenes.push({
                id: `s${Date.now()}`,
                prompt: '',
                seconds: 5,
                seed: ''
            });
            persist();
            renderScenes();
        }
    };

    head.append(left, quickAddBtn);
    container.append(head);

    // Body: Scenes list
    const body = document.createElement('div');
    body.className = 'fv-wizard-body fv-scenes-body';
    body.style.cssText = 'display: flex; flex-direction: column; gap: 8px; margin-top: 8px;';

    container.ontoggle = () => {
        state.enabled = container.open;
        persist();
    };

    function renderScenes() {
        body.replaceChildren();
        badge.textContent = `${state.scenes.length} ${t('scenes', '段')}`;
        quickAddBtn.disabled = state.scenes.length >= MAX_SCENES;

        // Story Bible hint for cross-scene visual consistency
        const bibleHint = document.createElement('div');
        bibleHint.style.cssText = 'font-size: 11px; color: #cbd5e1; background: rgba(99, 102, 241, 0.12); border: 1px dashed rgba(129, 140, 248, 0.35); border-radius: 6px; padding: 6px 10px; line-height: 1.4;';
        bibleHint.textContent = `💡 ${t('Story Bible: The main prompt above sets the persistent environment, character & lighting across all scenes.', '故事设定全局一致：上方的“描述画面”主提示词将作为全局角色、环境与光影基准，自动融入所有分镜。')}`;
        body.append(bibleHint);

        state.scenes.forEach((scene, index) => {
            const card = document.createElement('div');
            card.style.cssText = `
                background: rgba(15, 23, 42, 0.6);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 10px;
                display: flex;
                flex-direction: column;
                gap: 6px;
            `;

            const cardHead = document.createElement('div');
            cardHead.style.cssText = 'display: flex; align-items: center; justify-content: space-between;';

            const label = document.createElement('span');
            label.style.cssText = 'font-size: 12px; font-weight: 600; color: #a5b4fc;';
            label.textContent = index === 0
                ? `${t('Scene 1 (Start)', '分镜 1 (开篇)')}`
                : `${t(`Scene ${index + 1} (Continues Scene ${index})`, `分镜 ${index + 1} (承接分镜 ${index})`)}`;

            cardHead.append(label);

            if (state.scenes.length > 1) {
                const removeBtn = document.createElement('button');
                removeBtn.type = 'button';
                removeBtn.textContent = '✕';
                removeBtn.title = t('Remove scene', '移除分镜');
                removeBtn.style.cssText = 'background: transparent; border: none; color: #94a3b8; cursor: pointer; font-size: 13px; padding: 2px 6px;';
                removeBtn.onmouseover = () => { removeBtn.style.color = '#f87171'; };
                removeBtn.onmouseout = () => { removeBtn.style.color = '#94a3b8'; };
                removeBtn.onclick = () => {
                    state.scenes.splice(index, 1);
                    persist();
                    renderScenes();
                };
                cardHead.append(removeBtn);
            }
            card.append(cardHead);

            const promptId = `fv-scene-prompt-${scene.id || index}`;
            const textarea = document.createElement('textarea');
            textarea.id = promptId;
            textarea.name = promptId;
            textarea.placeholder = t(`Describe scene ${index + 1} action, setting and camera motion…`, `描述分镜 ${index + 1} 的画面、动作与运镜…`);
            textarea.value = scene.prompt || '';
            textarea.style.cssText = `
                width: 100%;
                min-height: 52px;
                box-sizing: border-box;
                background: rgba(0, 0, 0, 0.4);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                color: #f1f5f9;
                font-size: 12px;
                padding: 6px 8px;
                resize: vertical;
                font-family: inherit;
            `;
            textarea.oninput = () => {
                scene.prompt = textarea.value;
                persist();
            };
            card.append(textarea);

            const meta = document.createElement('div');
            meta.style.cssText = 'display: flex; align-items: center; gap: 14px; font-size: 11px; color: #94a3b8;';

            const secId = `fv-scene-sec-${scene.id || index}`;
            const secLabel = document.createElement('label');
            secLabel.htmlFor = secId;
            secLabel.style.cssText = 'display: flex; align-items: center; gap: 4px;';
            secLabel.textContent = t('Duration (s):', '时长(秒):');

            const secInput = document.createElement('input');
            secInput.id = secId;
            secInput.name = secId;
            secInput.type = 'number';
            secInput.min = '1.6';
            secInput.max = '60';
            secInput.step = '0.5';
            secInput.value = scene.seconds || 5;
            secInput.style.cssText = 'background: rgba(0, 0, 0, 0.4); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 4px; color: #f1f5f9; padding: 2px 5px; font-size: 11px; width: 52px;';
            secInput.onchange = () => {
                scene.seconds = Math.max(1.6, Number(secInput.value) || 2);
                persist();
            };
            secLabel.append(secInput);

            meta.append(secLabel);
            card.append(meta);
            body.append(card);
        });

        // Bottom "+ Add scene" dashed block
        if (state.scenes.length < MAX_SCENES) {
            const addCardBtn = document.createElement('button');
            addCardBtn.type = 'button';
            addCardBtn.style.cssText = `
                border: 1px dashed rgba(129, 140, 248, 0.35);
                background: rgba(99, 102, 241, 0.04);
                border-radius: 8px;
                color: #c7d2fe;
                padding: 8px;
                font-size: 12px;
                font-weight: 600;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 6px;
                transition: all 0.2s;
            `;
            addCardBtn.textContent = `+ ${t('Add another scene', '添加下一个分镜')}`;
            addCardBtn.onmouseover = () => {
                addCardBtn.style.borderColor = '#818cf8';
                addCardBtn.style.background = 'rgba(129, 140, 248, 0.1)';
            };
            addCardBtn.onmouseout = () => {
                addCardBtn.style.borderColor = 'rgba(129, 140, 248, 0.35)';
                addCardBtn.style.background = 'rgba(99, 102, 241, 0.04)';
            };
            addCardBtn.onclick = () => {
                state.scenes.push({
                    id: `s${Date.now()}`,
                    prompt: '',
                    seconds: 5,
                    seed: ''
                });
                persist();
                renderScenes();
            };
            body.append(addCardBtn);
        }

        // Bottom option: Pause to review
        const optionsRow = document.createElement('div');
        optionsRow.style.cssText = 'display: flex; align-items: center; justify-content: space-between; margin-top: 4px; padding-top: 6px; border-top: 1px solid rgba(255, 255, 255, 0.06); font-size: 12px; color: #cbd5e1;';

        const askId = `fv-scenes-ask-${node.id}`;
        const askLabel = document.createElement('label');
        askLabel.htmlFor = askId;
        askLabel.style.cssText = 'display: flex; align-items: center; gap: 6px; cursor: pointer;';

        const askCheckbox = document.createElement('input');
        askCheckbox.id = askId;
        askCheckbox.name = askId;
        askCheckbox.type = 'checkbox';
        askCheckbox.checked = Boolean(state.askAfterEach);
        askCheckbox.style.cssText = 'accent-color: #6366f1; cursor: pointer;';
        askCheckbox.onchange = () => {
            state.askAfterEach = askCheckbox.checked;
            persist();
        };
        askLabel.append(askCheckbox, document.createTextNode(t('Pause to review after each scene', '每段生成完成后暂停等待确认')));

        optionsRow.append(askLabel);
        body.append(optionsRow);

    }

    renderScenes();
    container.append(body);

    return {
        element: container,
        get enabled() { return Boolean(state.enabled); },
        get askAfterEach() { return Boolean(state.askAfterEach); },
        get scenes() { return state.scenes; },
        openAndAddScene() {
            state.enabled = true;
            container.open = true;
            if (state.scenes.length < MAX_SCENES) {
                state.scenes.push({
                    id: `s${Date.now()}`,
                    prompt: '',
                    seconds: 5,
                    seed: ''
                });
            }
            persist();
            renderScenes();
            const lastTextarea = body.querySelector('.fv-scene-card:last-of-type textarea');
            lastTextarea?.focus();
        },
        syncInitialPrompt(firstPrompt) {
            // Preserves scene prompts without erasing user entries; initialPrompt acts as global bible
            persist();
        },
        buildChainPayload(baseSeed = 0, sourceVideo = null, initialPrompt = '') {
            if (!state.enabled || !state.scenes.length) return null;
            const bible = (initialPrompt || '').trim();
            const blocks = state.scenes.map((scene, idx) => {
                const frames = secondsToLegalFrames(scene.seconds);
                const blockId = `b${idx + 1}`;
                const after = idx === 0 ? (sourceVideo ? { work: sourceVideo } : null) : `b${idx}`;
                const scenePrompt = (scene.prompt || '').trim();
                let combinedText = scenePrompt;
                if (bible) {
                    if (scenePrompt) {
                        if (!scenePrompt.startsWith(bible)) {
                            combinedText = `${bible}\n\n[Scene Action / Shots]: ${scenePrompt}`;
                        }
                    } else {
                        combinedText = bible;
                    }
                }
                return {
                    id: blockId,
                    after,
                    frames,
                    text: combinedText,
                    seed: (Number(scene.seed) || (baseSeed + idx))
                };
            });
            return {
                blocks,
                review: Boolean(state.askAfterEach),
                join: true
            };
        }
    };
}
