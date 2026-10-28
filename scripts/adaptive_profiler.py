"""Adaptive Hardware Profiler and Dynamic ETA Estimator for FreeVideo.

Calculates empirical rendering rates from local generation runs,
enabling accurate ETA timers and dynamically scaled timeouts tailored
to each end user's specific GPU and hardware profile.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
import time
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / '_project' / 'hardware_profile.json'


@dataclass
class HardwareBenchmark:
    device_name: str = "NVIDIA GeForce GTX 1070 Ti (8GB)"
    # Base rate in seconds per video-second at standard 448x320 T2VA
    base_sec_per_video_sec: float = 380.0
    # Surcharge factor when feeding a reference image (I2VA encoding)
    i2va_reference_multiplier: float = 1.25
    # Standard resolution baseline (448 x 320 = 143,360 pixels)
    baseline_pixels: int = 448 * 320
    # Minimum absolute safety timeout (seconds)
    min_timeout_seconds: int = 5400  # 90 minutes
    # Recorded run measurements: list of (video_seconds, pixels, is_i2va, elapsed_seconds)
    run_history: List[Dict[str, float]] = field(default_factory=list)

    @classmethod
    def load(cls) -> "HardwareBenchmark":
        if PROFILE_PATH.is_file():
            try:
                data = json.loads(PROFILE_PATH.read_text(encoding='utf-8'))
                return cls(**data)
            except Exception:
                pass
        bench = cls()
        bench.save()
        return bench

    def save(self) -> None:
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'device_name': self.device_name,
            'base_sec_per_video_sec': round(self.base_sec_per_video_sec, 2),
            'i2va_reference_multiplier': round(self.i2va_reference_multiplier, 2),
            'baseline_pixels': self.baseline_pixels,
            'min_timeout_seconds': self.min_timeout_seconds,
            'run_history': self.run_history[-50:],  # Retain last 50 runs
        }
        PROFILE_PATH.write_text(json.dumps(data, indent=2), encoding='utf-8')

    def record_run(self, duration_sec: float, width: int, height: int, has_ref: bool, elapsed_sec: float) -> None:
        """Update calibration using newly completed run stats."""
        pixels = width * height
        self.run_history.append({
            'duration_sec': duration_sec,
            'pixels': pixels,
            'is_i2va': 1.0 if has_ref else 0.0,
            'elapsed_sec': elapsed_sec,
            'timestamp': time.time(),
        })
        # Recalculate rolling average from recent runs
        recent = self.run_history[-15:]
        rates = []
        for r in recent:
            p_scale = r['pixels'] / self.baseline_pixels
            ref_factor = self.i2va_reference_multiplier if r.get('is_i2va') else 1.0
            normalized = r['elapsed_sec'] / (r['duration_sec'] * p_scale * ref_factor)
            if 100.0 <= normalized <= 900.0:
                rates.append(normalized)
        if rates:
            self.base_sec_per_video_sec = sum(rates) / len(rates)
        self.save()

    def estimate_render_time(self, duration_sec: float, width: int, height: int, has_ref: bool = False) -> float:
        """Estimate total seconds to render given target parameters."""
        pixel_scale = (width * height) / self.baseline_pixels
        ref_factor = self.i2va_reference_multiplier if has_ref else 1.0
        estimate = duration_sec * self.base_sec_per_video_sec * pixel_scale * ref_factor
        return max(300.0, estimate)

    def calculate_adaptive_timeout(self, duration_sec: float, width: int, height: int, has_ref: bool = False) -> int:
        """Compute safety timeout with 2.5x headroom over expected render time."""
        expected = self.estimate_render_time(duration_sec, width, height, has_ref)
        # Never timeout before at least 2.5x expected or 90 minutes
        return int(max(float(self.min_timeout_seconds), expected * 2.5))

    def format_progress(self, elapsed: int, expected: float) -> str:
        """Format an informative ETA countdown indicator for the console."""
        remaining = max(0, int(expected - elapsed))
        pct = min(99, int((elapsed / max(1.0, expected)) * 100))
        rem_min = remaining // 60
        rem_sec = remaining % 60
        exp_min = int(expected) // 60
        return f"  Rendering... {elapsed}s elapsed | Est. Total: ~{exp_min}m | ETA: ~{rem_min}m {rem_sec:02d}s remaining [{pct}%]"
