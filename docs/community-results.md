# Community results

Diagnostic reports from community users: the hardware, request, end-to-end time and version in each report, the version that includes the relevant optimization, and the estimated time after it.

| GPU | VRAM | RAM | Request | Version | End-to-end | Optimized in | Estimated after |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RTX 4060 Ti | 16 GB | 32 GB | 1920×1088 · 15 s · references · Light | 0.3.0 | 3306 s | 0.3.6 | about 2730 s (−580 s) |
| RTX 5060 | 8 GB | 16 GB | 1344×768 · 15 s · first frame · Light | 0.3.1 | 1367 s | 0.3.6 | about 1160 s (−210 s) |
| RTX 3070 Ti | 8 GB | 96 GB | 1344×768 · 10 s · references · Light | 0.3.1 | 749 s | 0.3.6 | about 690 s (−60 s) |
| RTX 4060 | 8 GB | 32 GB | 768×1344 · 15 s · references · Light | 0.3.4 | 1592 s | 0.3.10 | about 1450 s (−140 s) |
| RTX 5070 Laptop | 8 GB | 32 GB | 1344×768 · 5 s · Light | 0.3.4 | 555 s | 0.3.10 | about 485 s (−70 s) |
| RTX PRO 5000 | 72 GB | 128 GB | 768×1344 · 10 s · High | 0.3.4 | 552 s | — | — |
| RTX 4070 Laptop | 8 GB | 32 GB | 1344×768 · 8 s · references · Light | 0.3.5 | 1031 s | 0.3.10 | about 870 s (−160 s) |
| RTX 3070 Laptop | 8 GB | 32 GB | 1344×768 · 5 s · first frame · Light | 0.3.8 | 3821 s¹ | — | — |
| RTX 5080 Laptop | 16 GB | 32 GB | 1184×672 · 10 s · references · Light | 0.3.8 | 416 s | — | — |
| RTX 5090 | 32 GB | 32 GB | 1344×768 · 6.6 s · first frame · Max | 0.3.8 | 336 s | — | — |
| RTX 5070 Ti | 16 GB | 48 GB | 576×928 · 12.3 s · first frame · Medium | 0.3.8 | 472 s | — | — |
| RTX A5000 Laptop | 16 GB | 128 GB | 1344×768 · 10 s · first and last frames · Light | 0.3.8 | 1108 s¹ | — | — |
| RTX 4060 Ti | 16 GB | 32 GB | 1344×768 · 15 s · references · Max | 0.3.8 | 9829 s | 0.3.10 | about 2890 s (−6940 s) |

- Request: resolution · video length · inputs (first frame, first and last frames, or references; none means text to video) · quality level.
- Estimated after: derived from the report's stage times and from complete planned runs on our test machines (RTX 3090 on Windows, RTX PRO 6000 on Linux) under the reporting machine's VRAM and RAM limits, not from reruns on that machine.
- "—": later versions don't change how this machine runs the request.
- ¹ The GPU spent most of the run in thermal slowdown (RTX 3070 Laptop: 229 MHz on average at 89 °C; RTX A5000 Laptop: 882 MHz on average), so cooling limited the time.
