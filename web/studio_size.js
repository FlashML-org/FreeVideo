export function canvasDimensions(pixels, ratio) {
    const total = pixels * 1024 ** 2;
    return [Math.round(Math.sqrt(total * ratio) / 32) * 32, Math.round(Math.sqrt(total / ratio) / 32) * 32];
}

export function imageSizeSelection(pixels, ratio, width, height) {
    const options = [.10, .14, .25, .5, .75, .98, 1.5, 2];
    const matches = target => {
        const [w, h] = canvasDimensions(target, ratio);
        return w === width && h === height;
    };
    const legacy = ratio > 0 && (pixels === 1 || pixels === width * height / 1024 ** 2) && matches(1) && !matches(.98);
    if (legacy) options.splice(3, 0, 1);
    const nearest = legacy ? 1 : options.find(n => Math.abs(n - pixels) < .03);
    if (nearest === undefined) options.push(pixels);
    return {options, value: nearest ?? pixels};
}
