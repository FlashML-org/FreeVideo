# Linux

English · [中文](Linux.zh-CN.md)

FreeVideo runs on x86_64 Linux with an NVIDIA GPU and driver 580 or newer. On a desktop, the AppImage works like the Windows launcher. From a terminal or on a server, `./setup.sh` installs everything in one go, and the `freevideo` command opens it afterwards.

## Desktop: the AppImage

1. [Download FreeVideo-Linux-x86_64.AppImage](https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo-Linux-x86_64.AppImage).
2. Make it executable, either in the file's properties (**Allow executing file as program**) or with `chmod +x FreeVideo-Linux-x86_64.AppImage`, then open it.
3. Choose a folder for a new installation, or an existing ComfyUI, and click **Install & launch**. ComfyUI opens in the browser with the FreeVideo workspace.

FreeVideo then appears in the application menu and on the desktop, and later versions update the launcher by themselves, as on Windows. The AppImage needs a distribution from 2022 or newer (Ubuntu 22.04, Debian 12, Fedora 36, RHEL 9 or later); on older systems, use the terminal installation below. If the computer lacks the compiler FreeVideo builds its GPU acceleration with, the installation plan shows the command that installs it.

## Terminal: one command

```bash
git clone https://github.com/FlashML-org/FreeVideo.git && cd FreeVideo
./setup.sh
```

Setup checks the GPU, memory and disk, shows the plan, and after you confirm it installs the runtime, the models and ComfyUI. It then starts ComfyUI and opens FreeVideo in the browser. Everything stays in the FreeVideo folder.

Afterwards, type `freevideo` in any terminal to open FreeVideo again. ComfyUI keeps running in the background until you stop it:

| Command | What it does |
| --- | --- |
| `freevideo` | Open FreeVideo; starts ComfyUI first if it is not running |
| `freevideo status` | Show whether it runs, and its address |
| `freevideo stop` | Stop ComfyUI; while a video is generating, only with `--force` |
| `freevideo restart` | Restart ComfyUI |

After `git pull`, `freevideo` restarts ComfyUI with the update; while a video is generating it leaves ComfyUI running and asks you to restart later. ComfyUI uses port 8188, or the next free one if another program has it; `freevideo open --port 8190` chooses another.

## Server: use FreeVideo from another computer

On a server without a desktop, setup ends by printing the address and an SSH command. ComfyUI only accepts connections from the server itself, so other computers reach it through an SSH tunnel. On your own computer, run:

```bash
ssh -N -L 8188:127.0.0.1:8188 you@server
```

Then open http://127.0.0.1:8188/?freevideo=launch in your browser.

To connect from computers on your local network without SSH:

```bash
freevideo server --listen 0.0.0.0
```

This prints an access link with a token. Anyone with the link can use FreeVideo on the server; `freevideo server --listen 0.0.0.0 --new-token` replaces it, and `freevideo status` shows it again. ComfyUI itself still listens only on the server; a small proxy in front of it turns away every request without the token. The connection is not encrypted, so use it on a network you trust, or add `--tls-cert cert.pem --tls-key key.pem` for HTTPS.

To start FreeVideo with the server, add `--enable`; it becomes a systemd user service. To keep it running after you log out and start it at boot, also run `sudo loginctl enable-linger $USER` once. `freevideo server --disable` removes the service.

## Command-line generation

The engine also runs without ComfyUI:

```bash
./freevideo generate --prompt-file prompt.txt --out video.mp4
./test.sh
```

`./freevideo --help` lists every command.

## Troubleshooting

- **The AppImage does not start.** Some systems lack FUSE. Run it with `--appimage-extract-and-run`, or install the `fuse3` package.
- **ComfyUI does not start.** `freevideo status` names the last error. The logs are in the `launcher` folder of the installation (`service.log`, and `comfy-runs` for ComfyUI itself).
- **The address is in use.** Another program uses the port; start FreeVideo on another one with `freevideo open --port 8190`.
