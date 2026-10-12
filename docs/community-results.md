# Community results

Thanks to the community members who sent diagnostic reports. FreeVideo already runs on everything from 8 GB laptops to professional workstations; the table below summarizes the measurements and optimizations from these reports:

| GPU | VRAM | RAM | Resolution | Length | Quality | Version | Time | Optimized&nbsp;in | After |
|---|---:|---:|---:|---:|:---:|:---:|---:|:---:|---:|
| 4060&nbsp;Ti | 16&nbsp;GB | 32&nbsp;GB | 1920×1088 | 15&nbsp;s | Light | 0.3.0 | 3306&nbsp;s | 0.3.6 | 2730&nbsp;s&nbsp;(−580&nbsp;s) |
| 5060 | 8&nbsp;GB | 16&nbsp;GB | 1344×768 | 15&nbsp;s | Light | 0.3.1 | 1367&nbsp;s | 0.3.6 | 1160&nbsp;s&nbsp;(−210&nbsp;s) |
| 3070&nbsp;Ti | 8&nbsp;GB | 96&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.1 | 749&nbsp;s | 0.3.22 | 665&nbsp;s&nbsp;(−80&nbsp;s)⁶ |
| 4060 | 8&nbsp;GB | 32&nbsp;GB | 768×1344 | 15&nbsp;s | Light | 0.3.4 | 1592&nbsp;s | 0.3.10 | 1450&nbsp;s&nbsp;(−140&nbsp;s) |
| 5070&nbsp;Laptop | 8&nbsp;GB | 32&nbsp;GB | 1344×768 | 5&nbsp;s | Light | 0.3.4 | 555&nbsp;s | 0.3.10 | 485&nbsp;s&nbsp;(−70&nbsp;s) |
| PRO&nbsp;5000 | 72&nbsp;GB | 128&nbsp;GB | 768×1344 | 10&nbsp;s | High | 0.3.4 | 552&nbsp;s | 0.3.4 | Already&nbsp;optimal |
| 4070&nbsp;Laptop | 8&nbsp;GB | 32&nbsp;GB | 1344×768 | 8&nbsp;s | Light | 0.3.5 | 1031&nbsp;s | 0.3.21 | 830&nbsp;s&nbsp;(−200&nbsp;s) |
| 3070&nbsp;Laptop | 8&nbsp;GB | 32&nbsp;GB | 1344×768 | 5&nbsp;s | Light | 0.3.8 | 3821&nbsp;s | 0.3.8 | Thermal‑limited¹ |
| 5080&nbsp;Laptop | 16&nbsp;GB | 32&nbsp;GB | 1184×672 | 10&nbsp;s | Light | 0.3.8 | 416&nbsp;s | 0.3.21 | 385&nbsp;s&nbsp;(−30&nbsp;s) |
| 5090 | 32&nbsp;GB | 32&nbsp;GB | 1344×768 | 6.6&nbsp;s | Max | 0.3.8 | 336&nbsp;s | 0.3.8 | No&nbsp;report&nbsp;data |
| 5070&nbsp;Ti | 16&nbsp;GB | 48&nbsp;GB | 576×928 | 12.3&nbsp;s | Medium | 0.3.8 | 472&nbsp;s | int8&nbsp;model² | 420&nbsp;s&nbsp;(−50&nbsp;s) |
| A5000&nbsp;Laptop | 16&nbsp;GB | 128&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.8 | 1108&nbsp;s | 0.3.8 | Thermal‑limited¹ |
| 4060&nbsp;Ti | 16&nbsp;GB | 32&nbsp;GB | 1344×768 | 15&nbsp;s | Max | 0.3.8 | 9829&nbsp;s | 0.3.10 | 2890&nbsp;s&nbsp;(−6940&nbsp;s) |
| 5050&nbsp;Laptop | 8&nbsp;GB | 16&nbsp;GB | 960×544 | 3&nbsp;s | Light³ | 0.3.8 | 2313&nbsp;s | 0.3.18 | 2130&nbsp;s&nbsp;(−180&nbsp;s) |
| 3070 | 8&nbsp;GB | 32&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.13 | 899&nbsp;s | 0.3.15 | 880&nbsp;s&nbsp;(−20&nbsp;s) |
| 5060&nbsp;Ti | 16&nbsp;GB | 64&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.13 | 508&nbsp;s | 0.3.17 | 495&nbsp;s&nbsp;(−10&nbsp;s) |
| 4060&nbsp;Laptop | 8&nbsp;GB | 64&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.14 | 1296&nbsp;s | 0.3.21 | 1250&nbsp;s&nbsp;(−50&nbsp;s) |
| 5060 | 8&nbsp;GB | 16&nbsp;GB | 1280×736 | 15&nbsp;s | Light | 0.3.14 | 5285&nbsp;s | 0.3.21 | 4650&nbsp;s&nbsp;(−640&nbsp;s) |
| 4070&nbsp;Laptop | 8&nbsp;GB | 32&nbsp;GB | 960×544 | 5&nbsp;s | Light | 0.3.17 | 813&nbsp;s | 0.3.21 | 590&nbsp;s&nbsp;(−220&nbsp;s) |
| M4&nbsp;Max | Unified | 64&nbsp;GB | 768×1344 | 5&nbsp;s | Light | 0.3.5 | 2078&nbsp;s | 0.3.5 | Already&nbsp;optimal |
| 5090&nbsp;Laptop | 24&nbsp;GB | 64&nbsp;GB | 768×1344 | 2.3&nbsp;s | — | 0.3.18 | 132&nbsp;s | 0.3.21 | 122&nbsp;s&nbsp;(−10&nbsp;s)⁴ |
| 4060&nbsp;Ti | 16&nbsp;GB | 80&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.20 | 1122&nbsp;s | 0.3.21 | 1110&nbsp;s&nbsp;(−10&nbsp;s) |
| 5070&nbsp;Ti&nbsp;Laptop | 12&nbsp;GB | 32&nbsp;GB | 1344×768 | 10&nbsp;s | High | 0.3.20 | 1545&nbsp;s | 0.3.21 | 1310&nbsp;s&nbsp;(−240&nbsp;s) |
| 5060&nbsp;Ti | 16&nbsp;GB | 64&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.20 | 471&nbsp;s | 0.3.21 | 465&nbsp;s&nbsp;(−10&nbsp;s) |
| 4070&nbsp;SUPER | 12&nbsp;GB | 32&nbsp;GB | 1344×768 | 15&nbsp;s | High | 0.3.20 | 2966&nbsp;s | 0.3.21 | 2050&nbsp;s&nbsp;(−920&nbsp;s) |
| 4070&nbsp;Laptop | 8&nbsp;GB | 32&nbsp;GB | 544×960 | 3&nbsp;s | Light⁵ | 0.3.22 | 133&nbsp;s | 0.3.22 | Already&nbsp;optimal |
| M4&nbsp;Pro | Unified | 24&nbsp;GB | 960×544 | 10&nbsp;s | Light | 0.3.8 | 3457&nbsp;s | 0.3.8 | Already&nbsp;optimal |
| 5090 | 32&nbsp;GB | 64&nbsp;GB | 1376×768 | 10&nbsp;s | Light | 0.3.20 | 294&nbsp;s | 0.3.23 | 265&nbsp;s&nbsp;(−30&nbsp;s) |
| 5060&nbsp;Laptop | 8&nbsp;GB | 16&nbsp;GB | 960×544 | 3&nbsp;s | Light | 0.3.21 | 908&nbsp;s | 0.3.23 | 820&nbsp;s&nbsp;(−90&nbsp;s) |
| 3060 | 12&nbsp;GB | 64&nbsp;GB | 1184×672 | 10&nbsp;s | Light⁵ | 0.3.22 | 604&nbsp;s | 0.3.23 | 565&nbsp;s&nbsp;(−40&nbsp;s) |
| 4070 | 12&nbsp;GB | 32&nbsp;GB | 960×544 | 10&nbsp;s | Light | 0.3.22 | 340&nbsp;s | 0.3.22 | Already&nbsp;optimal |
| 5090 | 32&nbsp;GB | 64&nbsp;GB | 1344×768 | 10.8&nbsp;s | Light | 0.3.22 | 249&nbsp;s | 0.3.22 | Already&nbsp;optimal |
| 3090 | 24&nbsp;GB | 64&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.23 | 704&nbsp;s | 0.3.23 | Already&nbsp;optimal |
| 5060&nbsp;Ti | 16&nbsp;GB | 32&nbsp;GB | 1344×768 | 12.3&nbsp;s | Light | 0.3.23 | 573&nbsp;s | 0.3.23 | Already&nbsp;optimal |
| 5080 | 16&nbsp;GB | 64&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.23 | 240&nbsp;s | 0.3.23 | Already&nbsp;optimal |
| 3060&nbsp;Ti | 8&nbsp;GB | 48&nbsp;GB | 960×544 | 10&nbsp;s | Light | 0.3.22 | 508&nbsp;s | 0.3.24 | 490&nbsp;s&nbsp;(−20&nbsp;s) |
| 3070&nbsp;Laptop | 8&nbsp;GB | 24&nbsp;GB | 864×480 | 5&nbsp;s | Light | 0.3.22 | 677&nbsp;s | 0.3.24 | 645&nbsp;s&nbsp;(−30&nbsp;s) |
| 4060&nbsp;Laptop | 8&nbsp;GB | 16&nbsp;GB | 1312×768 | 10&nbsp;s | Light | 0.3.23 | 1024&nbsp;s | 0.3.23 | Already&nbsp;optimal |
| 3060 | 12&nbsp;GB | 96&nbsp;GB | 768×1344 | 5&nbsp;s | Light | 0.3.23 | 493&nbsp;s | 0.3.23 | Already&nbsp;optimal |
| M5&nbsp;Pro | Unified | 48&nbsp;GB | 960×544 | 5&nbsp;s | Light | 0.3.23 | 527&nbsp;s | 0.3.23 | Already&nbsp;optimal |
| 4090 | 24&nbsp;GB | 64&nbsp;GB | 1344×768 | 30.7&nbsp;s | Max | 0.3.23 | 5829&nbsp;s | 0.3.25 | 4850&nbsp;s&nbsp;(−980&nbsp;s) |
| 5060 | 8&nbsp;GB | 32&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.23 | 1279&nbsp;s | 0.3.25 | 1150&nbsp;s&nbsp;(−130&nbsp;s) |
| 3060 | 12&nbsp;GB | 32&nbsp;GB | 832×1248 | 10&nbsp;s | Light | 0.3.24 | 1668&nbsp;s | 0.3.24 | GPU&nbsp;shared⁷ |
| 5080 | 16&nbsp;GB | 64&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.24 | 312&nbsp;s | 0.3.24 | 278&nbsp;s&nbsp;(−34&nbsp;s) |

- Time: end to end, from submission until the video is saved.
- After: the estimated time, derived from the report's stage times and from complete planned runs on our test machines (RTX 3090 and RTX 5060 Ti on Windows, RTX PRO 6000 on Linux) under the reporting machine's VRAM and RAM limits, not from reruns on that machine.
- Already optimal: weight placement and transfers on this machine are already the fastest they can be; the time depends on the GPU's compute and the chosen quality level.
- ¹ Thermal-limited: the GPU spent most of the run in thermal slowdown (3070 Laptop: 229 MHz on average at 89 °C; A5000 Laptop: 882 MHz on average), so cooling limited the time.
- ² This machine still runs the FP8 model; the estimate is for the int8 model, a one-click upgrade in the launcher since 0.3.0.
- ³ Preview: only the first pass, at half resolution. With 16 GB of RAM most of the model is read from the drive at every step (about 80 MB/s on this machine), which takes most of the time.
- ⁴ The report had no diagnostic file; the estimate replays the plan for this machine's configuration and measures the same request on our test machines.
- ⁵ Second pass after a preview: the time covers the latent upscale, the three second-pass steps and decoding, not the first pass, which made the preview in an earlier request.
- ⁶ Measured on the same machine with 0.3.22.
- ⁷ GPU shared: the graphics card never became idle during the run (23% utilization and 74 W at its lowest), so it was also doing other work and FreeVideo's steps ran at a little over half their usual speed. With the card free, the same request takes about 1000 s.
