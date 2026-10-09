# Linux

[English](Linux.md) · 中文

FreeVideo 支持 x86_64 Linux，需要 NVIDIA 显卡和 580 及以上版本的驱动。桌面环境下，AppImage 的用法与 Windows 启动器相同；在终端或服务器上，运行 `./setup.sh` 即可一次装好，之后用 `freevideo` 命令打开。

## 桌面：AppImage

1. [下载 FreeVideo-Linux-x86_64.AppImage](https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo-Linux-x86_64.AppImage)。
2. 在文件属性中勾选**允许作为程序执行**，或运行 `chmod +x FreeVideo-Linux-x86_64.AppImage`，然后打开它。
3. 选择新安装的文件夹或已有的 ComfyUI，点击**安装并启动**。ComfyUI 会在浏览器中打开，并进入 FreeVideo 创作面板。

安装完成后，应用菜单和桌面上会出现 FreeVideo，启动器也会像 Windows 版一样自动更新。AppImage 需要 2022 年以后的发行版（Ubuntu 22.04、Debian 12、Fedora 36、RHEL 9 或更新版本）；更老的系统请使用下面的终端安装方式。如果系统缺少编译 GPU 加速所需的编译器，安装计划会给出安装它的命令。

## 终端：一条命令

```bash
git clone https://github.com/FlashML-org/FreeVideo.git && cd FreeVideo
./setup.sh
```

安装程序会检查显卡、内存和硬盘，显示安装计划，确认后安装运行环境、模型和 ComfyUI，然后启动 ComfyUI，并在浏览器中打开 FreeVideo。所有文件都在 FreeVideo 文件夹内。

之后在任意终端输入 `freevideo` 即可再次打开。ComfyUI 会在后台持续运行，直到手动停止：

| 命令 | 作用 |
| --- | --- |
| `freevideo` | 打开 FreeVideo；ComfyUI 没有运行时先启动它 |
| `freevideo status` | 查看是否在运行，以及访问地址 |
| `freevideo stop` | 停止 ComfyUI；有视频正在生成时需加 `--force` |
| `freevideo restart` | 重启 ComfyUI |

执行 `git pull` 后，再运行 `freevideo` 会重启 ComfyUI 加载新版本；如果有视频正在生成，则保持运行，并提示稍后重启。ComfyUI 默认使用 8188 端口，被其他程序占用时自动改用下一个空闲端口；也可以用 `freevideo open --port 8190` 指定端口。

## 服务器：从其他电脑使用

在没有桌面的服务器上，安装结束时会打印访问地址和一条 SSH 命令。ComfyUI 只接受服务器本机的连接，其他电脑通过 SSH 隧道访问。在自己的电脑上运行：

```bash
ssh -N -L 8188:127.0.0.1:8188 用户名@服务器
```

然后在浏览器中打开 http://127.0.0.1:8188/?freevideo=launch 。

如果希望局域网内的电脑不经过 SSH 直接访问：

```bash
freevideo server --listen 0.0.0.0
```

这会打印一个带访问令牌的链接，持有链接的人都可以使用这台服务器上的 FreeVideo；用 `freevideo server --listen 0.0.0.0 --new-token` 可以换一个新链接，`freevideo status` 可以再次查看。ComfyUI 本身仍然只监听服务器本机，前面的访问代理会拒绝所有不带令牌的请求。连接没有加密，请只在可信的网络中使用，或加上 `--tls-cert cert.pem --tls-key key.pem` 使用 HTTPS。

如需随服务器启动，加上 `--enable`，FreeVideo 会注册为 systemd 用户服务；若要在注销后保持运行、开机自动启动，还需运行一次 `sudo loginctl enable-linger $USER`。`freevideo server --disable` 会移除这个服务。

## 命令行生成

引擎也可以不经过 ComfyUI 直接运行：

```bash
./freevideo generate --prompt-file prompt.txt --out video.mp4
./test.sh
```

`./freevideo --help` 列出全部命令。

## 常见问题

- **AppImage 打不开**：部分系统缺少 FUSE。可以加上 `--appimage-extract-and-run` 运行，或安装 `fuse3`。
- **ComfyUI 没有启动**：运行 `freevideo status` 查看上次的错误。日志在安装目录的 `launcher` 文件夹中（`service.log`，ComfyUI 本身的日志在 `comfy-runs`）。
- **端口被占用**：有其他程序在使用该端口，可以用 `freevideo open --port 8190` 换一个端口。
