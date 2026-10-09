# Community results

Thanks to the community members who sent diagnostic reports. FreeVideo already runs on everything from 8 GB laptops to professional workstations; the table below summarizes the measurements and optimizations from these reports:

| GPU | VRAM | RAM | Resolution | Length | Quality | Version | Time | Optimized&nbsp;in | After |
|---|---:|---:|---:|---:|:---:|:---:|---:|:---:|---:|
| 4060&nbsp;Ti | 16&nbsp;GB | 32&nbsp;GB | 1920×1088 | 15&nbsp;s | Light | 0.3.0 | 3306&nbsp;s | 0.3.6 | 2730&nbsp;s&nbsp;(−580&nbsp;s) |
| 5060 | 8&nbsp;GB | 16&nbsp;GB | 1344×768 | 15&nbsp;s | Light | 0.3.1 | 1367&nbsp;s | 0.3.6 | 1160&nbsp;s&nbsp;(−210&nbsp;s) |
| 3070&nbsp;Ti | 8&nbsp;GB | 96&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.1 | 749&nbsp;s | 0.3.6 | 690&nbsp;s&nbsp;(−60&nbsp;s) |
| 4060 | 8&nbsp;GB | 32&nbsp;GB | 768×1344 | 15&nbsp;s | Light | 0.3.4 | 1592&nbsp;s | 0.3.10 | 1450&nbsp;s&nbsp;(−140&nbsp;s) |
| 5070&nbsp;Laptop | 8&nbsp;GB | 32&nbsp;GB | 1344×768 | 5&nbsp;s | Light | 0.3.4 | 555&nbsp;s | 0.3.10 | 485&nbsp;s&nbsp;(−70&nbsp;s) |
| PRO&nbsp;5000 | 72&nbsp;GB | 128&nbsp;GB | 768×1344 | 10&nbsp;s | High | 0.3.4 | 552&nbsp;s | 0.3.4 | Already&nbsp;optimal |
| 4070&nbsp;Laptop | 8&nbsp;GB | 32&nbsp;GB | 1344×768 | 8&nbsp;s | Light | 0.3.5 | 1031&nbsp;s | 0.3.10 | 870&nbsp;s&nbsp;(−160&nbsp;s) |
| 3070&nbsp;Laptop | 8&nbsp;GB | 32&nbsp;GB | 1344×768 | 5&nbsp;s | Light | 0.3.8 | 3821&nbsp;s | 0.3.8 | Thermal‑limited¹ |
| 5080&nbsp;Laptop | 16&nbsp;GB | 32&nbsp;GB | 1184×672 | 10&nbsp;s | Light | 0.3.8 | 416&nbsp;s | 0.3.8 | Already&nbsp;optimal |
| 5090 | 32&nbsp;GB | 32&nbsp;GB | 1344×768 | 6.6&nbsp;s | Max | 0.3.8 | 336&nbsp;s | 0.3.8 | No&nbsp;report&nbsp;data |
| 5070&nbsp;Ti | 16&nbsp;GB | 48&nbsp;GB | 576×928 | 12.3&nbsp;s | Medium | 0.3.8 | 472&nbsp;s | int8&nbsp;model² | 420&nbsp;s&nbsp;(−50&nbsp;s) |
| A5000&nbsp;Laptop | 16&nbsp;GB | 128&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.8 | 1108&nbsp;s | 0.3.8 | Thermal‑limited¹ |
| 4060&nbsp;Ti | 16&nbsp;GB | 32&nbsp;GB | 1344×768 | 15&nbsp;s | Max | 0.3.8 | 9829&nbsp;s | 0.3.10 | 2890&nbsp;s&nbsp;(−6940&nbsp;s) |
| 5050&nbsp;Laptop | 8&nbsp;GB | 16&nbsp;GB | 960×544 | 3&nbsp;s | Light³ | 0.3.8 | 2313&nbsp;s | 0.3.18 | 2130&nbsp;s&nbsp;(−180&nbsp;s) |
| 3070 | 8&nbsp;GB | 32&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.13 | 899&nbsp;s | 0.3.15 | 880&nbsp;s&nbsp;(−20&nbsp;s) |
| 5060&nbsp;Ti | 16&nbsp;GB | 64&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.13 | 508&nbsp;s | 0.3.17 | 495&nbsp;s&nbsp;(−10&nbsp;s) |
| 4060&nbsp;Laptop | 8&nbsp;GB | 64&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.14 | 1296&nbsp;s | 0.3.19 | 1265&nbsp;s&nbsp;(−30&nbsp;s) |
| 5060 | 8&nbsp;GB | 16&nbsp;GB | 1280×736 | 15&nbsp;s | Light | 0.3.14 | 5285&nbsp;s | 0.3.21 | 4650&nbsp;s&nbsp;(−640&nbsp;s) |
| 4070&nbsp;Laptop | 8&nbsp;GB | 32&nbsp;GB | 960×544 | 5&nbsp;s | Light | 0.3.17 | 813&nbsp;s | 0.3.21 | 590&nbsp;s&nbsp;(−220&nbsp;s) |
| M4&nbsp;Max | Unified | 64&nbsp;GB | 768×1344 | 5&nbsp;s | Light | 0.3.5 | 2078&nbsp;s | 0.3.5 | Already&nbsp;optimal |
| 5090&nbsp;Laptop | 24&nbsp;GB | 64&nbsp;GB | 768×1344 | 2.3&nbsp;s | — | 0.3.18 | 132&nbsp;s | 0.3.21 | 122&nbsp;s&nbsp;(−10&nbsp;s)⁴ |
| 5070&nbsp;Ti&nbsp;Laptop | 12&nbsp;GB | 32&nbsp;GB | 1344×768 | 10&nbsp;s | High | 0.3.20 | 1545&nbsp;s | 0.3.21 | 1310&nbsp;s&nbsp;(−240&nbsp;s) |
| 5060&nbsp;Ti | 16&nbsp;GB | 64&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.20 | 471&nbsp;s | 0.3.21 | 465&nbsp;s&nbsp;(−10&nbsp;s) |
| 4070&nbsp;SUPER | 12&nbsp;GB | 32&nbsp;GB | 1344×768 | 15&nbsp;s | High | 0.3.20 | 2966&nbsp;s | 0.3.21 | 2050&nbsp;s&nbsp;(−920&nbsp;s) |

- Time: end to end, from submission until the video is saved.
- After: the estimated time, derived from the report's stage times and from complete planned runs on our test machines (RTX 3090 and RTX 5060 Ti on Windows, RTX PRO 6000 on Linux) under the reporting machine's VRAM and RAM limits, not from reruns on that machine.
- Already optimal: weight placement and transfers on this machine are already the fastest they can be; the time depends on the GPU's compute and the chosen quality level.
- ¹ Thermal-limited: the GPU spent most of the run in thermal slowdown (3070 Laptop: 229 MHz on average at 89 °C; A5000 Laptop: 882 MHz on average), so cooling limited the time.
- ² This machine still runs the FP8 model; the estimate is for the int8 model, a one-click upgrade in the launcher since 0.3.0.
- ³ Preview: only the first pass, at half resolution. With 16 GB of RAM most of the model is read from the drive at every step (about 80 MB/s on this machine), which takes most of the time.
- ⁴ The report had no diagnostic file; the estimate replays the plan for this machine's configuration and measures the same request on our test machines.
