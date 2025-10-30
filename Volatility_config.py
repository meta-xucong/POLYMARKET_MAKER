# -*- coding: utf-8 -*-
"""Volatility_config
====================

集中管理套利脚本的配置参数与日志工具，提升配置可见度与输出一致性。

- **配置**：统一存放交易节奏、筛选阈值等默认值，供各模块引用；
- **日志**：提供 `log_event` 辅助函数，确保日志标签与格式一致。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, Dict, Mapping, Optional

LOG_TAGS: tuple[str, ...] = (
    "INIT",
    "CHOICE",
    "RUN",
    "PX",
    "HINT",
    "TRADE",
    "DONE",
    "WARN",
    "ERR",
    "CLAIM",
)


@dataclass(frozen=True)
class TradeConfig:
    """批量买入与盈利监控相关的默认参数。"""

    default_profit_percent: float = 5.0
    min_usdc_balance: float = 5.0
    buy_interval_seconds: float = 20.0
    check_interval_seconds: float = 600.0


@dataclass(frozen=True)
class FilterConfig:
    """市场筛选相关的默认参数。"""

    minimum_volume: float = 10_000.0
    min_time_to_end: timedelta = timedelta(minutes=5)
    max_time_to_end: timedelta = timedelta(days=7)
    min_yes_price: float = 0.95
    max_yes_price: float = 0.99
    blacklist_keywords: tuple[str, ...] = (
        "Bitcoin",
        "BTC",
        "ETH",
        "Ethereum",
        "Sol",
        "Solana",
        "Doge",
        "Dogecoin",
        "BNB",
        "Binance",
        "Cardano",
        "ADA",
        "XRP",
        "Ripple",
        "Matic",
        "Polygon",
        "Crypto",
        "Cryptocurrency",
        "Blockchain",
        "Token",
        "NFT",
        "DeFi",
        "vs",
        "odds",
        "score",
        "spread",
        "moneyline",
        "Esports",
        "CS2",
        "Cup",
        "Arsenal",
        "Liverpool",
        "Chelsea",
        "EPL",
        "PGA",
        "Tour Championship",
        "Scottie Scheffler",
        "Vitality",
        "MOUZ",
        "Falcons",
        "The MongolZ",
        "AL",
        "Houston",
        "Chicago",
        "New York",
    )
    request_timeout: int = 10
    request_limit: int = 500
    default_window_days: int = 3
    polymarket_api: str = "https://gamma-api.polymarket.com/markets"


TRADE_CONFIG = TradeConfig()
FILTER_CONFIG = FilterConfig()


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") or "0"
    if isinstance(value, timedelta):
        seconds = int(value.total_seconds())
        return f"{seconds}s"
    if isinstance(value, (list, tuple, set)):
        return "[" + ",".join(str(v) for v in value) + "]"
    if isinstance(value, Mapping):
        return "{" + ",".join(f"{k}={v}" for k, v in value.items()) + "}"
    return str(value)


def _format_context(context: Mapping[str, Any]) -> str:
    parts = []
    for key in sorted(context.keys()):
        parts.append(f"{key}={_format_value(context[key])}")
    return " ".join(parts)


def log_event(tag: str, message: str, *, context: Optional[Mapping[str, Any]] = None) -> None:
    """以统一格式输出日志信息。"""

    normalized = (tag or "").upper() or "INFO"
    prefix = f"[{normalized}]"
    if normalized not in LOG_TAGS:
        prefix = f"[{normalized}]"
    if context:
        print(f"{prefix} {message} | {_format_context(context)}")
    else:
        print(f"{prefix} {message}")


def config_snapshot() -> Dict[str, Any]:
    """导出当前配置快照，便于日志或调试。"""

    trade = asdict(TRADE_CONFIG)
    filter_snapshot: Dict[str, Any] = {
        "minimum_volume": FILTER_CONFIG.minimum_volume,
        "min_time_to_end": _format_value(FILTER_CONFIG.min_time_to_end),
        "max_time_to_end": _format_value(FILTER_CONFIG.max_time_to_end),
        "min_yes_price": FILTER_CONFIG.min_yes_price,
        "max_yes_price": FILTER_CONFIG.max_yes_price,
        "request_timeout": FILTER_CONFIG.request_timeout,
        "request_limit": FILTER_CONFIG.request_limit,
        "default_window_days": FILTER_CONFIG.default_window_days,
        "keyword_blacklist_size": len(FILTER_CONFIG.blacklist_keywords),
        "polymarket_api": FILTER_CONFIG.polymarket_api,
    }
    return {"trade": trade, "filter": filter_snapshot}


__all__ = [
    "TRADE_CONFIG",
    "FILTER_CONFIG",
    "LOG_TAGS",
    "log_event",
    "config_snapshot",
]
