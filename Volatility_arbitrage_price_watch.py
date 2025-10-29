# Volatility_arbitrage_price_watch.py
# -*- coding: utf-8 -*-
"""
只负责：解析 tokenIds + 订阅行情 + 每 N 秒节流输出一行（YES/NO 的 bid/ask/last）。
"""

from __future__ import annotations

import re, time, threading, json
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

try:
    import requests
except Exception:
    requests = None

GAMMA_API = "https://gamma-api.polymarket.com/markets"

def _is_url(s: str) -> bool:
    return s.startswith("http")

def _extract_market_slug(url: str) -> Optional[str]:
    # 同时兼容 market 页与 event 页
    m = re.search(r"/market/([^/?#]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/event/([^/?#]+)", url)
    return m.group(1) if m else None

def _gamma_fetch_market_by_slug(slug: str) -> Optional[dict]:
    if requests is None:
        print("[ERROR] 依赖 requests，请先安装： pip install requests")
        return None
    try:
        r = requests.get(GAMMA_API, params={"limit": 1, "slug": slug}, timeout=10)
        r.raise_for_status()
        arr = r.json()
        if isinstance(arr, list) and arr:
            return arr[0]
    except Exception as e:
        print(f"[WARN] gamma-api 查询失败: {e}")
    return None

def resolve_token_ids(source: str) -> Tuple[Optional[str], Optional[str], str]:
    """
    输入：Polymarket 市场 URL，或 'YES_id,NO_id'
    返回：(yes_token_id, no_token_id, label)
    """
    if _is_url(source):
        slug = _extract_market_slug(source)
        if not slug:
            raise ValueError("无法从 URL 解析出 market/event slug")
        m = _gamma_fetch_market_by_slug(slug)
        if not m:
            raise ValueError("gamma-api 未找到该市场（slug=%s）" % slug)
        token_ids_raw = m.get("clobTokenIds", "[]")
        token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else (token_ids_raw or [])
        yes_id = token_ids[0] if len(token_ids) > 0 else None
        no_id  = token_ids[1] if len(token_ids) > 1 else None
        title = m.get("question") or slug
        return yes_id, no_id, title

    if "," in source:
        a, b = source.split(",", 1)
        return (a.strip() or None), (b.strip() or None), "manual-token-ids"

    raise ValueError("未识别的输入。请传入 Polymarket 市场 URL，或 'YES_id,NO_id'。")

# ============ 监听 & 节流输出 ============
def watch_prices(source: str, interval: int = 1):
    """
    通过 ws_watch_by_ids 静默订阅行情，每 interval 秒输出一次中文精简行。
    """
    yes_id, no_id, label = resolve_token_ids(source)
    asset_ids = [x for x in (yes_id, no_id) if x]

    # 延迟导入，避免循环依赖
    from Volatility_arbitrage_main_ws import ws_watch_by_ids

    print(f"[INIT] 数据源: {label}")
    print(f"[INIT] YES token_id = {yes_id}")
    print(f"[INIT] NO  token_id = {no_id}")
    print(f"[RUN] 每 {interval}s 输出一次：YES/NO 买/卖（bid/ask），含最近成交价 price。Ctrl+C 结束。")

    latest: Dict[str, Dict[str, Any]] = {aid: {} for aid in asset_ids}

    def _on_event(ev: Dict[str, Any]):
        # 兼容两种格式：
        #   1) {"event_type":"price_change", "price_changes":[...]}
        #   2) {"price_changes":[...]}（无 event_type）
        if not isinstance(ev, dict):
            return
        if ev.get("event_type") == "price_change":
            pcs = ev.get("price_changes", [])
        elif "price_changes" in ev:
            pcs = ev.get("price_changes", [])
        else:
            return

        for pc in pcs:
            aid = pc.get("asset_id")
            if not aid:
                continue
            latest[aid] = {
                "price": pc.get("price"),
                "best_bid": pc.get("best_bid"),
                "best_ask": pc.get("best_ask"),
            }

    # 启动 WS（静默，不打印原始事件）
    t = threading.Thread(target=ws_watch_by_ids, kwargs={
        "asset_ids": asset_ids,
        "label": label,
        "on_event": _on_event,
        "verbose": False
    }, daemon=True)
    t.start()

    # —— 节流输出 ——
    try:
        while True:
            y = latest.get(yes_id, {})
            n = latest.get(no_id, {})
            ts = datetime.now().strftime("%H:%M:%S")
            if y or n:
                parts = []
                if y:
                    parts.append(f"YES价={y.get('price')} (买盘{y.get('best_bid')} / 卖盘{y.get('best_ask')})")
                if n:
                    parts.append(f"NO价={n.get('price')} (买盘{n.get('best_bid')} / 卖盘{n.get('best_ask')})")
                if parts:
                    print(f"[{ts}] " + " | ".join(parts))
            time.sleep(max(1, int(interval)))
    except KeyboardInterrupt:
        print("\n[EXIT] 用户中断，程序结束。")

# ============ CLI ============
def _parse_cli(argv):
    """
    --source "<url 或 YES,NO>"
    --interval 1
    """
    source = None
    interval = 1
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--source" and i+1 < len(argv):
            source = argv[i+1]; i += 2; continue
        if a == "--interval" and i+1 < len(argv):
            try:
                interval = int(argv[i+1])
            except Exception:
                interval = 1
            i += 2; continue
        i += 1
    return source, interval

if __name__ == "__main__":
    import sys as _sys
    src, itv = _parse_cli(_sys.argv[1:])
    if not src:
        print("用法: python Volatility_arbitrage_price_watch.py --source <url 或 YES_id,NO_id> [--interval 1]")
        _sys.exit(1)
    watch_prices(src, itv)
