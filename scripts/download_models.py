"""Model weight verification and retrieval tool for FreeVideo / MiniMax H3.

Validates model repositories, verifies network connectivity to Hugging Face,
and assists users with downloading weights to their designated ComfyUI models folder.
"""
import argparse
import os
from pathlib import Path
import sys
import time
import urllib.request
import urllib.error
from typing import Dict, List, Tuple

# Essential Model Repositories for MiniMax H3 / FreeVideo
MODEL_REPOSITORIES: List[Dict[str, str]] = [
    {
        "name": "MiniMax H3 / VDN Base Weights (Edge Int8 / FP8)",
        "repo": "OpenVDN/vdn-minimax-h3-edge",
        "url": "https://huggingface.co/OpenVDN/vdn-minimax-h3-edge",
        "size_gb": "14.5 GB",
        "required": True,
        "dest_subdir": "models/checkpoints"
    },
    {
        "name": "MiniMax H3 Text Encoder (T5-XXL / Decoders)",
        "repo": "t8star/Vdn-Minimax-H3-Comfy",
        "url": "https://huggingface.co/t8star/Vdn-Minimax-H3-Comfy",
        "size_gb": "9.8 GB",
        "required": True,
        "dest_subdir": "models/clip"
    },
    {
        "name": "MiniMax H3 Latent Upscaler & VAE",
        "repo": "LBH-123-AI/Minimax_h3_latent_Upscaler",
        "url": "https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler",
        "size_gb": "4.2 GB",
        "required": True,
        "dest_subdir": "models/vae"
    }
]


def check_url_connectivity(url: str, timeout: int = 5) -> Tuple[bool, str]:
    """Performs HTTP HEAD/GET request to verify model link operational status."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'FreeVideo-ModelDownloader/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status in (200, 301, 302):
                return True, "ONLINE (Reachable)"
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as he:
        return False, f"HTTP Error: {he.code}"
    except urllib.error.URLError as ue:
        return False, f"Network Error: {ue.reason}"
    except Exception as ex:
        return False, f"Error: {ex}"


def verify_all_repositories() -> bool:
    """Verifies operational status of all external weight repositories."""
    print("===================================================")
    print("[FreeVideo] Model Weight Repository Pre-Flight Check")
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("===================================================\n")

    all_passed = True
    for item in MODEL_REPOSITORIES:
        print(f">> Verifying: {item['name']}")
        print(f"   URL:  {item['url']}")
        print(f"   Size: ~{item['size_gb']}")
        reachable, status_text = check_url_connectivity(item['url'])
        if reachable:
            print(f"   Status: [OK] {status_text}\n")
        else:
            print(f"   Status: [FAILED] {status_text}")
            print(f"   [NOTICE] If HuggingFace is blocked or degraded in your region,")
            print(f"   please consider downloading via a proxy or official mirror: https://hf-mirror.com/{item['repo']}\n")
            all_passed = False

    return all_passed


def main() -> None:
    parser = argparse.ArgumentParser(description="FreeVideo Model Weights Downloader and Verifier")
    parser.add_argument("--check-only", action="store_true", help="Only verify connectivity and status without downloading")
    args = parser.parse_args()

    ok = verify_all_repositories()
    if not ok:
        print("===================================================")
        print("[WARNING] One or more model repositories could not be contacted.")
        print("Please check your internet connection or use a mirror.")
        print("===================================================")
        sys.exit(1)
    else:
        print("===================================================")
        print("[SUCCESS] All model repository links are operational!")
        print("To install or verify models locally, launch the FreeVideo GUI Launcher")
        print("or use ComfyUI Model Manager.")
        print("===================================================")
        sys.exit(0)


if __name__ == "__main__":
    main()
