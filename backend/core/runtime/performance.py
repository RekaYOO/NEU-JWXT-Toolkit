"""Bounded, process-local performance observations.

This is diagnostic state rather than business data.  It intentionally keeps
only timings and bounded labels, has no persistence, and exposes no network
endpoint.  Structured logs remain the durable operational record.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Deque


@dataclass(frozen=True)
class PerformanceSummary:
    count: int
    average_ms: float
    p95_ms: float
    maximum_ms: float


_lock = threading.Lock()
_samples: OrderedDict[str, Deque[float]] = OrderedDict()
_MAX_SERIES = 128
_MAX_SAMPLES = 512


def observe_performance(series: str, duration_ms: float) -> None:
    key = str(series or "unknown")[:160]
    value = max(0.0, float(duration_ms))
    with _lock:
        bucket = _samples.get(key)
        if bucket is None:
            if len(_samples) >= _MAX_SERIES:
                _samples.popitem(last=False)
            bucket = deque(maxlen=_MAX_SAMPLES)
            _samples[key] = bucket
        else:
            _samples.move_to_end(key)
        bucket.append(value)


def performance_snapshot() -> dict[str, PerformanceSummary]:
    with _lock:
        copied = {key: tuple(values) for key, values in _samples.items()}
    result: dict[str, PerformanceSummary] = {}
    for key, values in copied.items():
        if not values:
            continue
        ordered = sorted(values)
        p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
        result[key] = PerformanceSummary(
            count=len(values),
            average_ms=round(sum(values) / len(values), 2),
            p95_ms=round(ordered[p95_index], 2),
            maximum_ms=round(ordered[-1], 2),
        )
    return result
