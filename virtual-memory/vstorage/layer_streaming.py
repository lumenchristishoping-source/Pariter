"""Reproduces HANDBOOK.md Experiment 19 (model layer-streaming).

A multi-layer model's weights are quantized (32-bit float -> 8-bit int)
and compressed, one layer at a time. Running the model means decompressing
and loading one layer, using it, freeing it, then moving to the next -
so peak RAM tracks ONE layer's size, not the whole model, regardless of
how many layers the model has.
"""

from __future__ import annotations

import ctypes
import gc
import lzma

import numpy as np

from .measure import rss_anon_mb

try:
    _libc = ctypes.CDLL("libc.so.6")
except OSError:  # pragma: no cover - non-glibc platform
    _libc = None


def _release_freed_memory() -> None:
    """gc.collect() drops Python's own references, but glibc's malloc
    can still hold freed arenas back from the OS (a known allocator
    quirk with repeated large alloc/free cycles - not a leak, but it
    makes RSS readings misleading unless flushed). malloc_trim(0)
    forces genuinely-freed memory back to the OS so measurements
    reflect what's actually still held, not what the allocator is
    idly hanging onto for reuse.
    """
    gc.collect()
    if _libc is not None:
        _libc.malloc_trim(0)


def quantize_layer(weights: np.ndarray) -> tuple[bytes, float, float]:
    """32-bit float -> 8-bit int, linearly scaled to the layer's own
    min/max range. Returns (quantized_bytes, scale, zero_point) so the
    layer can be dequantized back to (approximately) the original values.
    """
    w_min, w_max = float(weights.min()), float(weights.max())
    scale = (w_max - w_min) / 255.0 if w_max > w_min else 1.0
    quantized = np.round((weights - w_min) / scale).astype(np.uint8)
    return quantized.tobytes(), scale, w_min


def dequantize_layer(data: bytes, shape: tuple, scale: float, zero_point: float) -> np.ndarray:
    q = np.frombuffer(data, dtype=np.uint8).reshape(shape)
    return q.astype(np.float32) * scale + zero_point


class StreamedModel:
    """Holds a multi-layer model compressed and quantized, at rest. Runs
    it layer-by-layer, decompressing/loading exactly one layer at a time.
    """

    def __init__(self, layer_shapes: list[tuple]):
        self._shapes = layer_shapes
        self._compressed: list[bytes] = []
        self._scales: list[float] = []
        self._zero_points: list[float] = []
        self._raw_total_bytes = 0
        self._held_total_bytes = 0

        rng = np.random.default_rng(42)
        for shape in layer_shapes:
            weights = rng.standard_normal(shape).astype(np.float32)
            self._raw_total_bytes += weights.nbytes

            q_bytes, scale, zero_point = quantize_layer(weights)
            compressed = lzma.compress(q_bytes, preset=6)

            self._compressed.append(compressed)
            self._scales.append(scale)
            self._zero_points.append(zero_point)
            self._held_total_bytes += len(compressed)

            del weights, q_bytes  # Pitfall 3: don't let generation-time
            _release_freed_memory()  # arrays linger past the layer they built.

    @property
    def raw_size_mb(self) -> float:
        return self._raw_total_bytes / (1024 * 1024)

    @property
    def held_size_mb(self) -> float:
        return self._held_total_bytes / (1024 * 1024)

    def run(self) -> dict:
        """Runs every layer in sequence, freeing each before loading the
        next. Samples RSS after each layer to show peak tracks one
        layer's size, not the running total.
        """
        peak_layer_rss = []
        for i, shape in enumerate(self._shapes):
            q_bytes = lzma.decompress(self._compressed[i])
            layer = dequantize_layer(q_bytes, shape, self._scales[i], self._zero_points[i])

            _ = float(layer.sum())  # stand-in for "the layer does work"

            del q_bytes, layer  # free before the next layer loads
            _release_freed_memory()
            peak_layer_rss.append(rss_anon_mb())

        return {
            "layers_run": len(self._shapes),
            "raw_size_mb": self.raw_size_mb,
            "held_size_mb": self.held_size_mb,
            "compression_ratio": self.raw_size_mb / max(self.held_size_mb, 1e-9),
            "rss_during_run_mb": peak_layer_rss,
        }
