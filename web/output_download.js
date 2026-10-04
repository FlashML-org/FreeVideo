// The server supplies the download name and streams the existing file, keeping
// large videos out of a browser-side Blob and preserving playback/asset paths.
export function outputDownloadURL(api, file) {
    const saved = /^FreeVideo\/(\d{4}-\d{2}-\d{2}\/[a-f0-9]{32})\/video\.mp4$/.exec(file);
    if (saved) return api.apiURL('/freevideo/library/download?' + new URLSearchParams({id: saved[1]}));
    const split = file.lastIndexOf('/');
    return api.apiURL('/view?' + new URLSearchParams({filename: file.slice(split + 1), subfolder: split < 0 ? '' : file.slice(0, split), type: 'output'}));
}
