"""Importable Triton stand-in for Pascal / Windows when the real package is absent."""


class Config:
    def __init__(self, *args, **kwargs):
        pass


def jit(fn=None, **kwargs):
    return fn if callable(fn) else (lambda f: f)


def autotune(*args, **kwargs):
    return lambda fn: fn


def cdiv(a, b):
    return (a + b - 1) // b


def next_power_of_2(n):
    p = 1
    while p < n:
        p *= 2
    return p


from . import language
from . import knobs