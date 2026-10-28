// Shared creation/editor aspect names and the creation panel's 32-pixel sizing.
export const frameShapes = t => [
    ['16:9', t('Landscape', '横屏'), 16 / 9],
    ['9:16', t('Portrait', '竖屏'), 9 / 16],
    ['1:1', t('Square', '方形'), 1],
];

// Pascal-first canvases that already produced clips on 8 GB. Width and height
// are multiples of 32 and at least 256, as the H3 VAE requires.
export const PASCAL_PRESETS = Object.freeze({
    '16:9': [[448, 256], [512, 288], [576, 320], [640, 352]],
    '9:16': [[256, 448], [288, 512], [320, 576], [352, 640]],
    '1:1': [[256, 256], [320, 320], [384, 384], [448, 448]],
    landscape: [[448, 320], [448, 256], [512, 288], [576, 320]],
    portrait: [[320, 448], [256, 448], [288, 512], [320, 576]],
    square: [[256, 256], [320, 320], [384, 384], [448, 448]],
});

export const PASCAL_QUICK_SIZES = Object.freeze([
    ['landscape', 448, 320],
    ['portrait', 320, 448],
    ['square', 320, 320],
    ['landscape', 448, 256],
    ['square', 256, 256],
]);

export function frameSize(pixels, ratio) {
    const total = pixels * 1024 ** 2;
    return {width: Math.round(Math.sqrt(total * ratio) / 32) * 32,
        height: Math.round(Math.sqrt(total / ratio) / 32) * 32};
}

export function presetsForShape(id) {
    return PASCAL_PRESETS[id] || PASCAL_PRESETS.landscape;
}
