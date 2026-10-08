# Community results

Diagnostic reports from community users: the hardware, request, end-to-end time and version in each report, the version that includes the relevant optimization, and the estimated time after it.

| GPU | VRAM | RAM | Request | End-to-end | Version | Optimized in | Estimated after |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RTX 4060 Ti | 16 GB | 32 GB | 1920×1088 · 15 s · references | 3306 s | 0.3.0 | 0.3.6 | 2560–2900 s (−410 to −740 s) |
| RTX 5060 | 8 GB | 16 GB | 1344×768 · 15 s · first frame | 1367 s | 0.3.1 | 0.3.6 | 1120–1190 s (−180 to −250 s) |
| RTX 3070 Ti | 8 GB | 96 GB | 1344×768 · 10 s · references | 749 s | 0.3.1 | 0.3.6 | 675–705 s (−45 to −75 s) |
| RTX 4060 | 8 GB | 32 GB | 768×1344 · 15 s · references | 1592 s | 0.3.4 | 0.3.10 | about 1450 s (−145 s) |
| RTX 5070 Laptop | 8 GB | 32 GB | 1344×768 · 5 s | 555 s | 0.3.4 | 0.3.10 | 480–490 s (−65 to −75 s) |
| RTX PRO 5000 | 72 GB | 128 GB | 768×1344 · 10 s · single pass, 16 steps | 552 s | 0.3.4 | — | — |
| RTX 4070 Laptop | 8 GB | 32 GB | 1344×768 · 8 s · references | 1031 s | 0.3.5 | 0.3.10 | about 870 s (−160 s) |
| RTX 3070 Laptop | 8 GB | 32 GB | 1344×768 · 5 s · first frame | 3821 s¹ | 0.3.8 | — | — |
| RTX 5080 Laptop | 16 GB | 32 GB | 1184×672 · 10 s · references | 416 s | 0.3.8 | — | — |
| RTX 5090 | 32 GB | 32 GB | 1344×768 · 6.6 s · first frame · single pass, 20 steps | 336 s | 0.3.8 | — | — |
| RTX 5070 Ti | 16 GB | 48 GB | 576×928 · 12.3 s · first frame · single pass, 12 steps | 472 s | 0.3.8 | — | — |
| RTX A5000 Laptop | 16 GB | 128 GB | 1344×768 · 10 s · first and last frames | 1108 s¹ | 0.3.8 | — | — |
| RTX 4060 Ti | 16 GB | 32 GB | 1344×768 · 15 s · references · single pass, 20 steps | 9829 s | 0.3.8 | 0.3.10 | about 2890 s (−6940 s) |

- Request: resolution · video length · inputs (first frame, first and last frames, or references; none means text to video). Without a step count, the request used the default two-pass sampling (8 + 3 steps); "single pass, N steps" is a setting the user chose.
- Estimated after: derived from the report's stage times and from complete planned runs on our test machines (RTX 3090 on Windows, RTX PRO 6000 on Linux) under the reporting machine's VRAM and RAM limits, not from reruns on that machine.
- "—": later versions don't change how this machine runs the request.
- ¹ The GPU spent most of the run in thermal slowdown (RTX 3070 Laptop: 229 MHz on average at 89 °C; RTX A5000 Laptop: 882 MHz on average), so cooling limited the time.
