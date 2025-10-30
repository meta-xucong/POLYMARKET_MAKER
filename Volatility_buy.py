# Volatility_buy.py
# -*- coding: utf-8 -*-
"""
WS 分层版 · 买单执行器（= 现有 auto_buy.py 语义）
- 职责单一：按上层传入的 token_id / price / size 立即买入
- 下单语义：FAK（Fill And Kill，≈ IOC）
- 规范化规则（沿用老版 2/4/2 精度“三件套”）：
    * 价格：2 位小数，向上取整（不低于 bestAsk，便于立即成交）
    * 份数：4 位小数，向上取整（默认整股；不足 $1 时强制整股兜底）
    * 金额：2 位小数，向上取整（仅用于日志核对，SDK 仍以 price×size 计算）
- 默认份数留空时，按 `ceil(1/price)` 计算整股，确保名义金额 ≥ $1。
- 仅供被调用，不建议独立运行。暴露 API：execute_auto_buy(client, token_id, price, size)
"""

from decimal import Decimal, ROUND_UP
from typing import Tuple

from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY


def _q4_up(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.0001"), rounding=ROUND_UP)  # 份数四位，上取


def _q2_up(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_UP)  # 价格/金额两位，上取


def _ceil_int(x: Decimal) -> Decimal:
    return x.quantize(Decimal("1"), rounding=ROUND_UP)


def _min_legal_pair(price: float, size: float) -> Tuple[float, float, float]:
    """生成最小合法组合（满足 2/4/2 精度并保证名义金额 ≥ $1）。"""

    p = _q2_up(Decimal(str(price)))
    if p <= 0:
        raise ValueError("price must be positive")

    s_hint = Decimal(str(size))
    s_need = _ceil_int(Decimal("1.00") / p)  # 整股兜底（0dp）

    if s_hint <= 0:
        eff_size = s_need
    else:
        if s_hint == s_hint.to_integral_value():
            eff_size = _ceil_int(s_hint)
        else:
            eff_size = _q4_up(s_hint)
        if eff_size < s_need:
            eff_size = s_need

    eff_size = _q4_up(eff_size)
    maker = _q2_up(p * eff_size)

    return float(p), float(eff_size), float(maker)


def execute_auto_buy(client, token_id: str, price: float, size: float):
    eff_price, eff_size, maker = _min_legal_pair(price, size)
    print(
        "[Volatility_buy] 规范化 -> base_price=%s | hint_size=%s | eff_price=%.2f | eff_size=%.4f | maker=%.2f"
        % (price, size, eff_price, eff_size, maker)
    )
    order = OrderArgs(token_id=str(token_id), side=BUY, price=float(eff_price), size=float(eff_size))
    print(f"[Volatility_buy] create_order BUY token_id={token_id} price={eff_price} size={eff_size}")
    signed = client.create_order(order)
    print("[Volatility_buy] post_order type=FAK")
    return client.post_order(signed, OrderType.FAK)  # 如需“全成或撤”，可改为 OrderType.FOK
