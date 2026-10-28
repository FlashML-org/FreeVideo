"""Minimal knobs surface so triton_compat.activate() is a no-op-safe import."""
import os


def getenv(key, default=None):
    return os.environ.get(key, default)
