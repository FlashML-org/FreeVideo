# Community results

Thanks to the community members who sent diagnostic reports. FreeVideo already runs on everything from 8 GB laptops to professional workstations, and the table below collects their measurements: hardware, request, version and end-to-end time, plus the version that includes the relevant optimization and the estimated time after it.

| RTX&nbsp;GPU | VRAM | RAM | Resolution | Length | Quality | Version | Time | Optimized&nbsp;in | After |
|---|---:|---:|---:|---:|:---:|:---:|---:|:---:|---:|
| 4060&nbsp;Ti | 16&nbsp;GB | 32&nbsp;GB | 1920×1088 | 15&nbsp;s | Light | 0.3.0 | 3306&nbsp;s | 0.3.6 | 2730&nbsp;s&nbsp;(−580&nbsp;s) |
| 5060 | 8&nbsp;GB | 16&nbsp;GB | 1344×768 | 15&nbsp;s | Light | 0.3.1 | 1367&nbsp;s | 0.3.6 | 1160&nbsp;s&nbsp;(−210&nbsp;s) |
| 3070&nbsp;Ti | 8&nbsp;GB | 96&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.1 | 749&nbsp;s | 0.3.6 | 690&nbsp;s&nbsp;(−60&nbsp;s) |
| 4060 | 8&nbsp;GB | 32&nbsp;GB | 768×1344 | 15&nbsp;s | Light | 0.3.4 | 1592&nbsp;s | 0.3.10 | 1450&nbsp;s&nbsp;(−140&nbsp;s) |
| 5070&nbsp;Laptop | 8&nbsp;GB | 32&nbsp;GB | 1344×768 | 5&nbsp;s | Light | 0.3.4 | 555&nbsp;s | 0.3.10 | 485&nbsp;s&nbsp;(−70&nbsp;s) |
| PRO&nbsp;5000 | 72&nbsp;GB | 128&nbsp;GB | 768×1344 | 10&nbsp;s | High | 0.3.4 | 552&nbsp;s | — | Already&nbsp;optimal |
| 4070&nbsp;Laptop | 8&nbsp;GB | 32&nbsp;GB | 1344×768 | 8&nbsp;s | Light | 0.3.5 | 1031&nbsp;s | 0.3.10 | 870&nbsp;s&nbsp;(−160&nbsp;s) |
| 3070&nbsp;Laptop | 8&nbsp;GB | 32&nbsp;GB | 1344×768 | 5&nbsp;s | Light | 0.3.8 | 3821&nbsp;s | — | Thermal‑limited¹ |
| 5080&nbsp;Laptop | 16&nbsp;GB | 32&nbsp;GB | 1184×672 | 10&nbsp;s | Light | 0.3.8 | 416&nbsp;s | — | Already&nbsp;optimal |
| 5090 | 32&nbsp;GB | 32&nbsp;GB | 1344×768 | 6.6&nbsp;s | Max | 0.3.8 | 336&nbsp;s | — | No&nbsp;report&nbsp;data |
| 5070&nbsp;Ti | 16&nbsp;GB | 48&nbsp;GB | 576×928 | 12.3&nbsp;s | Medium | 0.3.8 | 472&nbsp;s | int8&nbsp;model² | 420&nbsp;s&nbsp;(−50&nbsp;s) |
| A5000&nbsp;Laptop | 16&nbsp;GB | 128&nbsp;GB | 1344×768 | 10&nbsp;s | Light | 0.3.8 | 1108&nbsp;s | — | Thermal‑limited¹ |
| 4060&nbsp;Ti | 16&nbsp;GB | 32&nbsp;GB | 1344×768 | 15&nbsp;s | Max | 0.3.8 | 9829&nbsp;s | 0.3.10 | 2890&nbsp;s&nbsp;(−6940&nbsp;s) |

- After: the estimated time, derived from the report's stage times and from complete planned runs on our test machines (RTX 3090 on Windows, RTX PRO 6000 on Linux) under the reporting machine's VRAM and RAM limits, not from reruns on that machine.
- Already optimal: weight placement and transfers on this machine are already the fastest they can be; the time depends on the GPU's compute and the chosen quality level.
- ¹ Thermal-limited: the GPU spent most of the run in thermal slowdown (3070 Laptop: 229 MHz on average at 89 °C; A5000 Laptop: 882 MHz on average), so cooling limited the time.
- ² This machine still runs the FP8 model; the estimate is for the int8 model, a one-click upgrade in the launcher since 0.3.0.
