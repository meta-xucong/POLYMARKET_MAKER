
# Volatility_sell.py
# -*- coding: utf-8 -*-
"""
WS 分层版 · 卖单执行器（5步让利 · FOK）
= 提炼自 auto_sell_pnl.py，但将“顶盘口价(bid)”改为由上层传参（ref_price=bestBid），与 Volatility_buy 对齐：
    execute_auto_sell(client, token_id, price, size)

设计要点：
- 职责：只执行卖出；不做 PnL/筛选/通知。
- 价格锚：使用上层传入的 bestBid 快照（ref_price），逐档让利。
- 执行语义：FOK（Fill Or Kill），最多 5 档；默认让利阶梯：0%、1%、2%、3%、4%。
- 精度：
    * 价格 round 到 4 位小数（与原 auto_sell_pnl 习惯一致）；
    * 数量 2 位小数向下取整（floor 2dp）；<0.01 则跳过。
- 退出条件：任一轮返回 status in {"success","matched"} 立即结束；否则返回最后一次响应（或 None）。
"""

import math
from typing import Optional, Dict, Any, Iterable

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import SELL

__all__ = ["execute_auto_sell"]


def _floor_2dp(x: float) -> float:
    return math.floor(float(x) * 100.0) / 100.0


def _ladder_prices(ref_price: float, ladder_bps: Iterable[int]) -> Iterable[float]:
    """
    给定参考价 ref_price（通常为 bestBid），按 bps 阶梯生成降价序列。
    例如 ladder_bps=(0,100,200,300,400) => 0%、1%、2%、3%、4% 让利。
    价格按 4dp round（与原文件保持一致）。
    """
    p0 = float(ref_price)
    for bps in ladder_bps:
        pct = max(0.0, float(bps)) / 10000.0
        yield round(p0 * (1.0 - pct), 4)


def execute_auto_sell(
    client: ClobClient,
    token_id: str,
    price: float,     # 上层传入的 bestBid 快照（ref_price）
    size: float,      # 待卖出的总数量
    attempts: int = 5,
    ladder_bps: Iterable[int] = (0, 100, 200, 300, 400),
) -> Optional[Dict[str, Any]]:
    """
    与 Volatility_buy 对齐的签名：execute_auto_sell(client, token_id, price, size)
    - price: 上层传入的参考价（建议用当前 bestBid）
    - size : 待卖出数量
    """
    # 数量按 2dp 向下取整
    size_real = _floor_2dp(size)
    if size_real < 0.01:
        print("[Volatility_sell] size < 0.01 after 2dp floor, skip.")
        return None

    # 生成最多 attempts 次的价格阶梯（按 4dp round）
    prices = list(_ladder_prices(price, ladder_bps))
    if attempts > 0:
        prices = prices[:attempts]
    else:
        prices = []

    last_resp: Optional[Dict[str, Any]] = None

    for idx, px in enumerate(prices, start=1):
        print(f"[Volatility_sell] Attempt {idx}/{len(prices)} - price={px} size={size_real} (ref={price})")
        try:
            order_args = OrderArgs(price=float(px), size=float(size_real), side=SELL, token_id=str(token_id))
            signed = client.create_order(order_args)
            resp = client.post_order(signed, OrderType.FOK)  # 保持 FOK 语义
            last_resp = resp

            status = (resp or {}).get("status", "").lower()
            print(f"[Volatility_sell] resp.status={status}")
            if status in {"success", "matched"}:
                return resp
        except Exception as e:
            print(f"[Volatility_sell] Order error: {e!r}")

    print("[Volatility_sell] All attempts failed.")
    return last_resp
