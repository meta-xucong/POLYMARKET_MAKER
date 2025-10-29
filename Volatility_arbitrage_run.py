# Volatility_arbitrage_run.py
# -*- coding: utf-8 -*-
"""
运行入口（新版：事件解析 + 触发买入→成交确认→盈利用 FOK 卖出）
- 事件页 /event/<slug>：列出子问题并选择（与老版一致）
- 新增：
  1) 交互输入：买入份数（留空按 $1 反推）、买入触发价（对标 ask）、盈利百分比（默认 5%）
  2) 触发满足 → BUY/FAK（价5dp、量4dp均向上取）；只有“确认成交”才进入持仓状态
  3) 成交后 → SELL/FOK 以 0/1/2/3/4% 五档让利尝试（价4dp、量2dp，向下取）
- 其余模块（Client/WS/打印）一字不动
"""
from __future__ import annotations
import sys, time, threading, re, math
from typing import Dict, Any, Tuple, List, Optional
from decimal import Decimal, ROUND_UP, ROUND_DOWN
import requests
from Volatility_buy import execute_auto_buy  # 与老版本一致：BUY 精度规范化交由执行器（5/4/2）

# ========== 1) Client：优先 ws 版，回退 rest 版 ==========
def _get_client():
    try:
        from Volatility_arbitrage_main_ws import get_client  # 优先
        return get_client()
    except Exception as e1:
        try:
            from Volatility_arbitrage_main_rest import get_client  # 退回
            return get_client()
        except Exception as e2:
            print("[ERR] 无法导入 get_client：", e1, "|", e2)
            sys.exit(1)

# ========== 2) 保留 price_watch 的单市场解析函数（先尝试） ==========
try:
    from Volatility_arbitrage_price_watch import resolve_token_ids
except Exception as e:
    print("[ERR] 无法从 Volatility_arbitrage_price_watch 导入 resolve_token_ids：", e)
    sys.exit(1)

# ========== 3) 行情订阅（未动） ==========
try:
    from Volatility_arbitrage_main_ws import ws_watch_by_ids
except Exception as e:
    print("[ERR] 无法从 Volatility_arbitrage_main_ws 导入 ws_watch_by_ids：", e)
    sys.exit(1)

GAMMA_ROOT = "https://gamma-api.polymarket.com"

# ===== 旧版解析器（复刻 + 极小修正） =====
def _parse_yes_no_ids_literal(source: str) -> Tuple[Optional[str], Optional[str]]:
    parts = [x.strip() for x in source.split(",")]
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1]
    return None, None

def _extract_event_slug(s: str) -> str:
    m = re.search(r"/event/([^/?#]+)", s)
    if m: return m.group(1)
    s = s.strip()
    if s and ("/" not in s) and ("?" not in s) and ("&" not in s):
        return s
    return ""

def _http_json(url: str, params=None) -> Optional[Any]:
    try:
        r = requests.get(url, params=params or {}, timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def _list_markets_under_event(event_slug: str) -> List[dict]:
    if not event_slug:
        return []
    # A) /events?slug=<slug>
    data = _http_json(f"{GAMMA_ROOT}/events", params={"slug": event_slug, "closed": "false"})
    evs = []
    if isinstance(data, dict) and "data" in data:
        evs = data["data"]
    elif isinstance(data, list):
        evs = data
    if isinstance(evs, list):
        for ev in evs:
            mkts = ev.get("markets") or []
            if mkts:
                return mkts
    # B) /markets?search=<slug> 精确过滤 eventSlug
    data = _http_json(f"{GAMMA_ROOT}/markets", params={"limit": 200, "active": "true", "search": event_slug})
    mkts = []
    if isinstance(data, dict) and "data" in data:
        mkts = data["data"]
    elif isinstance(data, list):
        mkts = data
    if isinstance(mkts, list):
        return [m for m in mkts if str(m.get("eventSlug") or "") == str(event_slug)]
    return []

def _fetch_market_by_slug(market_slug: str) -> Optional[dict]:
    return _http_json(f"{GAMMA_ROOT}/markets/slug/{market_slug}")

def _pick_market_subquestion(markets: List[dict]) -> dict:
    print("[CHOICE] 该事件下存在多个子问题，请选择其一，或直接粘贴具体子问题URL：")
    for i, m in enumerate(markets):
        title = m.get("title") or m.get("question") or m.get("slug")
        end_ts = m.get("endDate") or m.get("endTime") or ""
        mslug = m.get("slug") or ""
        url = f"https://polymarket.com/market/{mslug}" if mslug else "(no slug)"
        print(f"  [{i}] {title}  (end={end_ts})  -> {url}")
    while True:
        s = input("请输入序号或粘贴URL：").strip()
        if s.startswith(("http://", "https://")):
            return {"__direct_url__": s}
        if s.isdigit():
            idx = int(s)
            if 0 <= idx < len(markets):
                return markets[idx]
        print("请输入有效序号或URL。")

def _tokens_from_market_obj(m: dict) -> Tuple[str, str, str]:
    title = m.get("title") or m.get("question") or m.get("slug") or ""
    yes_id = no_id = ""
    ids = m.get("clobTokenIds") or m.get("clobTokens")
    if isinstance(ids, (list, tuple)) and len(ids) >= 2:
        return str(ids[0]), str(ids[1]), title
    outcomes = m.get("outcomes") or []
    if outcomes and isinstance(outcomes[0], dict):
        for o in outcomes:
            name = (o.get("name") or o.get("outcome") or "").strip().lower()
            tid = o.get("tokenId") or o.get("clobTokenId") or ""
            if not tid: continue
            if name in ("yes", "y", "true"): yes_id = str(tid)
            elif name in ("no", "n", "false"): no_id = str(tid)
        if yes_id and no_id:
            return yes_id, no_id, title
    return yes_id, no_id, title

def _resolve_with_fallback(source: str) -> Tuple[str, str, str]:
    # 1) "YES_id,NO_id"
    y, n = _parse_yes_no_ids_literal(source)
    if y and n: return y, n, "(Manual IDs)"
    # 2) 先尝试旧解析器（单一市场 URL/slug）
    try:
        y1, n1, title1 = resolve_token_ids(source)
        if y1 and n1:
            return y1, n1, title1
    except Exception:
        pass
    # 3) 事件页/事件 slug 回退链路
    event_slug = _extract_event_slug(source)
    if not event_slug:
        raise ValueError("无法从输入中提取事件 slug，且直接解析失败。")
    mkts = _list_markets_under_event(event_slug)
    if not mkts:
        raise ValueError(f"未在事件 {event_slug} 下检索到子问题列表。")
    chosen = _pick_market_subquestion(mkts)
    if "__direct_url__" in chosen:
        y2, n2, title2 = resolve_token_ids(chosen["__direct_url__"])
        if y2 and n2: return y2, n2, title2
        raise ValueError("无法从粘贴的URL解析出 tokenId。")
    y3, n3, title3 = _tokens_from_market_obj(chosen)
    if y3 and n3:
        return y3, n3, title3
    slug2 = chosen.get("slug") or ""
    if slug2:
        # 兜底：拉完整市场详情；若还不行，再把 /market/<slug> 丢给旧解析器
        m_full = _fetch_market_by_slug(slug2)
        if m_full:
            y4, n4, title4 = _tokens_from_market_obj(m_full)
            if y4 and n4: return y4, n4, title4
        y5, n5, title5 = resolve_token_ids(f"https://polymarket.com/market/{slug2}")
        if y5 and n5: return y5, n5, title5
    raise ValueError("子问题未包含 tokenId，且兜底解析失败。")

# ====== 下单执行工具 ======
def _ceil(x: float, dp: int) -> float:
    q = Decimal(str(x)).quantize(Decimal("1." + "0"*dp), rounding=ROUND_UP)
    return float(q)

def _floor(x: float, dp: int) -> float:
    q = Decimal(str(x)).quantize(Decimal("1." + "0"*dp), rounding=ROUND_DOWN)
    return float(q)

def _normalize_buy_pair(price: float, size: float) -> Tuple[float, float]:
    # 价格 2dp 上取；份数 4dp 上取（默认整股；用户手输允许到 4dp）
    return _ceil(price, 2), _ceil(size, 4)

def _normalize_sell_pair(price: float, size: float) -> Tuple[float, float]:
    # 价格 4dp；份数 2dp（下单时再 floor 一次，确保不超）
    return _floor(price, 4), _floor(size, 2)

def _place_buy_fak(client, token_id: str, price: float, size: float) -> Dict[str, Any]:
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY
    eff_p, eff_s = _normalize_buy_pair(price, size)
    order = OrderArgs(token_id=str(token_id), side=BUY, price=float(eff_p), size=float(eff_s))
    signed = client.create_order(order)
    return client.post_order(signed, OrderType.FAK)

def _place_sell_fok(client, token_id: str, price: float, size: float) -> Dict[str, Any]:
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import SELL
    eff_p, eff_s = _normalize_sell_pair(price, size)
    order = OrderArgs(token_id=str(token_id), side=SELL, price=float(eff_p), size=float(eff_s))
    signed = client.create_order(order)
    return client.post_order(signed, OrderType.FOK)

# ===== 主流程 =====
def main():
    client = _get_client()
    print("[INIT] ClobClient 就绪。")
    print('请输入 Polymarket 市场 URL，或 "YES_id,NO_id"：')
    source = input().strip()
    if not source:
        print("[ERR] 未输入，退出。"); return
    try:
        yes_id, no_id, title = _resolve_with_fallback(source)
    except Exception as e:
        print("[ERR] 无法解析目标：", e); return
    print(f"[INFO] 市场/子问题标题: {title}")
    print(f"[INFO] 解析到 tokenIds: YES={yes_id} | NO={no_id}")
    print('请选择方向（YES/NO），回车确认：')
    side = input().strip().upper()
    if side not in ("YES", "NO"):
        print("[ERR] 方向非法，退出。"); return
    token_id = yes_id if side == "YES" else no_id

    # ===== 新增：买卖参数 =====
    print("请输入买入份数（留空=按 $1 反推）：")
    size_in = input().strip()
    print("请输入买入触发价（对标 ask，如 0.35）：")
    buy_px_in = input().strip()
    print("请输入卖出盈利百分比（默认 5 表示 +5%）：")
    prof_in = input().strip() or "5"
    try:
        profit_pct = float(prof_in) / 100.0
    except:
        print("[ERR] 盈利百分比非法，退出。"); return

    latest: Dict[str, Dict[str, Any]] = {token_id: {}}

    # 订阅行情
    def _on_event(ev: Dict[str, Any]):
        if ev.get("event_type") != "price_change": return
        for pc in ev.get("price_changes", []):
            if pc.get("asset_id") == token_id:
                latest[token_id] = {"price": pc.get("price"),
                                    "best_bid": pc.get("best_bid"),
                                    "best_ask": pc.get("best_ask")}
    t = threading.Thread(target=ws_watch_by_ids,
                         kwargs={"asset_ids":[token_id],
                                 "label":f"{title} ({side})",
                                 "on_event":_on_event,
                                 "verbose":False},
                         daemon=True)
    t.start()

    # 等行情加载到第一个快照
    print("[RUN] 监听行情中…（每 1s 输出一次）")
    while not latest.get(token_id):
        time.sleep(0.2)

    # 计算 size（默认按 $1 反推份数）
    def _calc_size_by_1dollar(ask_px: float) -> float:
        if not ask_px or ask_px <= 0: return 1.0
        s = 1.0 / ask_px
        return float(Decimal(str(s)).quantize(Decimal("1"), rounding=ROUND_UP))  # 整数股，上取

    # ===== 交易状态机（单轮模式）=====
    buy_px = None
    if buy_px_in:
        try:
            buy_px = float(buy_px_in)
        except:
            print("[ERR] 触发价非法，退出。"); return

    bought = False
    buy_fill_px = None
    size = None

    while True:
        snap = latest.get(token_id) or {}
        bid = float(snap.get("best_bid") or 0.0)
        ask = float(snap.get("best_ask") or 0.0)
        last = float(snap.get("price") or 0.0)

        print(f"[PX] token_id={token_id} | bid={bid:.2f} ask={ask:.2f} last={last:.2f}")
        time.sleep(1.0)

        # 未持仓 → 判断是否触发买入
        if not bought:
            if buy_px is not None and ask > 0 and ask <= buy_px:
                # 份数
                if size_in:
                    try:
                        size = float(size_in)
                    except:
                        print("[ERR] 份数非法，退出。"); return
                else:
                    size = _calc_size_by_1dollar(ask)
                    print(f"[HINT] 未指定份数，按 $1 反推（整股） -> size={size}")

                # 规范化（价2dp、量≤4dp；默认整股）
                eff_p, eff_s = _normalize_buy_pair(ask, size)

                # 兜底：名义额 ≥ $1（整股）
                if eff_p * eff_s < 1.0:
                    eff_s = float(Decimal(str(1.0/eff_p)).quantize(Decimal("1"), rounding=ROUND_UP))

                # BUY FAK
                resp = execute_auto_buy(client=client, token_id=token_id, price=eff_p, size=eff_s)  # 交由执行器做 5/4/2 规范化并 FAK 下单
                print(f"[TRADE][BUY] resp={resp}")
                status = (resp or {}).get("status","").lower()
                if status in {"success","matched"}:
                    bought = True
                    buy_fill_px = float(eff_p)  # 以下单价估计（更严：可读取回包或 positions）
                    print(f"[STATE] 成功买入：px={buy_fill_px:.2f} size={eff_s}")
                else:
                    print("[STATE] 买入未成交，继续监听…")
            continue

        # 已持仓 → 判断是否达成止盈（对标 bid）
        if bought and buy_fill_px and bid > 0:
            target = buy_fill_px * (1.0 + profit_pct)
            if bid >= target:
                # 发起五档让利的 FOK 卖出（price 传 bestBid 作参考价）
                from Volatility_sell import execute_auto_sell
                sell_resp = execute_auto_sell(client=client, token_id=token_id, price=bid, size=eff_s)
                print(f"[TRADE][SELL] resp={sell_resp}")
                sstatus = (sell_resp or {}).get("status","").lower()
                if sstatus in {"success","matched"}:
                    print("[DONE] 单轮完成，退出。")
                    break
                else:
                    print("[WARN] 卖出未成交，继续监听…")

if __name__ == "__main__":
    main()