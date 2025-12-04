"""Shared interaction rate limiter and cache with basic monitoring.

设计目标：
- 统一的节流入口，确保同一 channel 的发送间隔至少为 1s。
- 为重复查询提供 TTL 缓存（默认 ≥1s），降低高频轮询。
- 每类交互记录 sent/delayed/dropped 计数，便于观察负载下降趋势。
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple


@dataclass
class _CacheEntry:
    value: Any
    timestamp: float
    ttl: float


class InteractionGuard:
    """Coordinate outbound interactions with per-channel throttling and caching."""

    def __init__(self, min_interval: float = 1.0) -> None:
        self.min_interval = max(min_interval, 0.0)
        self._last_sent: Dict[str, float] = {}
        self._stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"sent": 0, "delayed": 0, "dropped": 0}
        )
        self._cache: Dict[Any, _CacheEntry] = {}

    def wait_turn(self, channel: str) -> float:
        now = time.monotonic()
        last = self._last_sent.get(channel)
        if last is not None:
            elapsed = now - last
            if elapsed < self.min_interval:
                delay = self.min_interval - elapsed
                self._stats[channel]["delayed"] += 1
                time.sleep(delay)
                now = time.monotonic()
        self._last_sent[channel] = now
        self._stats[channel]["sent"] += 1
        return now

    def wait_and_call(self, channel: str, fn: Callable[[], Any]) -> Any:
        self.wait_turn(channel)
        return fn()

    def cached_call(
        self,
        cache_key: Any,
        ttl: float,
        producer: Callable[[], Any],
        *,
        channel: Optional[str] = None,
    ) -> Tuple[Any, bool]:
        now = time.monotonic()
        entry = self._cache.get(cache_key)
        if entry and now - entry.timestamp < ttl:
            if channel:
                self._stats[channel]["dropped"] += 1
            return entry.value, True

        if channel:
            self.wait_turn(channel)
            now = time.monotonic()

        value = producer()
        self._cache[cache_key] = _CacheEntry(value=value, timestamp=now, ttl=ttl)
        return value, False

    def record_drop(self, channel: str) -> None:
        self._stats[channel]["dropped"] += 1

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        return {k: dict(v) for k, v in self._stats.items()}

    def format_snapshot(self, prefix: str = "") -> str:
        parts = []
        for channel in sorted(self._stats):
            stats = self._stats[channel]
            parts.append(
                f"{channel}:sent={stats['sent']},delay={stats['delayed']},drop={stats['dropped']}"
            )
        if not parts:
            return f"{prefix} <no-interactions>" if prefix else "<no-interactions>"
        summary = "; ".join(parts)
        return f"{prefix} {summary}" if prefix else summary

    def log_snapshot(self, prefix: str = "[INTERACTION]") -> None:
        print(self.format_snapshot(prefix=prefix))


interaction_guard = InteractionGuard(min_interval=1.0)
