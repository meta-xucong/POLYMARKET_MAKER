"""Volatility_fliter
====================

基于 `poly_filter.py` 的筛选逻辑升级版。集中管理配置参数，
并提供公共函数供主流程调用。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Sequence

import requests

# === 可配置参数（集中管理） ===
# 最低成交量阈值（严格大于该值）
MINIMUM_VOLUME: float = 10_000.0
# 市场最短剩余时间（用于排除即将到期的市场）
MIN_TIME_TO_END: timedelta = timedelta(minutes=5)
# 市场最远截止时间（含边界）
MAX_TIME_TO_END: timedelta = timedelta(days=7)
# YES 价格允许区间（开区间，排除边界值）
MIN_YES_PRICE: float = 0.95
MAX_YES_PRICE: float = 0.99
# 关键词黑名单（匹配时区分大小写，沿用旧版默认值，可按需增删）
BLACKLIST_KEYWORDS: Sequence[str] = (
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
# API 请求配置
POLYMARKET_API: str = "https://gamma-api.polymarket.com/markets"
DEFAULT_WINDOW_DAYS: int = 3
REQUEST_TIMEOUT: int = 10
REQUEST_LIMIT: int = 500


# === 工具函数 ===

def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_outcome_prices(raw: Any) -> List[float]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, Iterable):
        prices: List[float] = []
        for item in raw:
            try:
                prices.append(float(item))
            except (TypeError, ValueError):
                prices.append(float("nan"))
        return prices
    return []


def _parse_token_ids(raw: Any) -> List[str | None]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, Iterable):
        return [str(item) if item is not None else None for item in raw]
    return []


def _is_blacklisted(text: str) -> bool:
    return any(keyword in text for keyword in BLACKLIST_KEYWORDS)


# === 核心筛选逻辑 ===

def market_is_eligible(market: Dict[str, Any], *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)

    if market.get("closed") is True:
        return False
    if market.get("acceptingOrders") is False:
        return False

    try:
        volume = float(market.get("volume") or 0.0)
    except (TypeError, ValueError):
        volume = 0.0
    if volume <= MINIMUM_VOLUME:
        return False

    end_dt = _parse_datetime(market.get("endDate"))
    if end_dt is None:
        return False
    if end_dt <= now + MIN_TIME_TO_END:
        return False
    if end_dt > now + MAX_TIME_TO_END:
        return False

    outcome_prices = _parse_outcome_prices(market.get("outcomePrices", []))
    if not outcome_prices:
        return False
    yes_price = outcome_prices[0]
    try:
        yes_price_f = float(yes_price)
    except (TypeError, ValueError):
        return False
    if not (MIN_YES_PRICE < yes_price_f < MAX_YES_PRICE):
        return False

    haystack_parts = [
        market.get("question", ""),
        market.get("groupItemTitle", ""),
        market.get("slug", ""),
        market.get("description", ""),
    ]
    events = market.get("events") or []
    if events:
        first_event = events[0] or {}
        haystack_parts.append(first_event.get("title", ""))
        haystack_parts.append(first_event.get("description", ""))
    haystack = " ".join(filter(None, haystack_parts))
    if _is_blacklisted(haystack):
        return False

    return True


def fetch_markets(end_min_dt: datetime, end_max_dt: datetime, *, window_days: int = DEFAULT_WINDOW_DAYS) -> List[Dict[str, Any]]:
    all_markets: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    cur_start = end_min_dt
    delta = timedelta(seconds=1)

    while cur_start <= end_max_dt:
        cur_end = min(cur_start + timedelta(days=window_days), end_max_dt)
        params = {
            "limit": REQUEST_LIMIT,
            "order": "endDate",
            "ascending": "true",
            "active": "true",
            "end_date_min": cur_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date_max": cur_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "volume_num_min": int(MINIMUM_VOLUME),
            "closed": "false",
        }
        try:
            response = requests.get(POLYMARKET_API, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            chunk = response.json()
        except Exception:
            chunk = []

        for market in chunk:
            market_id = market.get("id") or market.get("slug")
            if not market_id:
                continue
            if market_id in seen_ids:
                continue
            seen_ids.add(market_id)
            all_markets.append(market)

        if len(chunk) >= REQUEST_LIMIT and window_days > 1:
            sub_markets = fetch_markets(cur_start, cur_end, window_days=max(1, window_days // 2))
            for market in sub_markets:
                market_id = market.get("id") or market.get("slug")
                if not market_id:
                    continue
                if market_id in seen_ids:
                    continue
                seen_ids.add(market_id)
                all_markets.append(market)

        cur_start = cur_end + delta

    return all_markets


def build_market_summary(market: Dict[str, Any]) -> Dict[str, Any]:
    outcome_prices = _parse_outcome_prices(market.get("outcomePrices", []))
    token_ids = _parse_token_ids(market.get("clobTokenIds", []))

    yes_price = outcome_prices[0] if len(outcome_prices) > 0 else None
    no_price = outcome_prices[1] if len(outcome_prices) > 1 else None
    yes_token_id = token_ids[0] if len(token_ids) > 0 else None
    no_token_id = token_ids[1] if len(token_ids) > 1 else None

    events = market.get("events") or []
    event_slug = (events[0] or {}).get("slug") if events else None
    market_slug = market.get("slug", "") or ""
    if event_slug and market_slug:
        url = f"https://polymarket.com/event/{event_slug}/{market_slug}"
    elif market_slug:
        url = f"https://polymarket.com/market/{market_slug}"
    else:
        url = "https://polymarket.com/markets"

    return {
        "url": url,
        "question": market.get("question", ""),
        "group": market.get("groupItemTitle", ""),
        "yes_price": yes_price,
        "no_price": no_price,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "startDate": market.get("startDate", ""),
        "endDate": market.get("endDate", ""),
        "volume": market.get("volume", 0),
        "description": market.get("description", ""),
        "orderMinSize": market.get("orderMinSize"),
        "bestAsk": market.get("bestAsk"),
        "bestBid": market.get("bestBid"),
    }


def get_filtered_markets(*, now: datetime | None = None) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    end_min_dt = now + MIN_TIME_TO_END
    end_max_dt = now + MAX_TIME_TO_END

    raw_markets = fetch_markets(end_min_dt, end_max_dt)
    eligible_markets: List[Dict[str, Any]] = []
    for market in raw_markets:
        if not market_is_eligible(market, now=now):
            continue
        eligible_markets.append(build_market_summary(market))
    return eligible_markets


if __name__ == "__main__":
    markets = get_filtered_markets()
    print(f"通过 Volatility_fliter 筛选：{len(markets)}")
    for item in markets[:5]:
        print("-", item.get("question"), "→", item.get("url"))
