
# Volatility_arbitrage_strategy.py
# 极简策略：当 best_ask ≤ buy_price_threshold 发出 BUY；
#           当 best_bid ≥ entry_price * (1 + profit_ratio) 发出 SELL；
# 仅产出信号，不负责 size / 精度 / 下单执行。需上游成交回调推进状态。

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


class ActionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"   # 保留类型以便状态查询时使用


@dataclass
class StrategyConfig:
    token_id: str
    buy_price_threshold: float                  # 触发买入的目标价格（对标 best_ask）
    profit_ratio: float = 0.05                  # 卖出目标的收益比例（对标 best_bid）

    # 轻量防抖：同一方向的“待确认”状态下不重复发信号
    disable_duplicate_signal: bool = True

    # 可选价域守门（避免极端边界价误触）
    min_price: Optional[float] = 0.0
    max_price: Optional[float] = 1.0


@dataclass
class Action:
    action: ActionType
    token_id: str
    reason: str
    ref_price: float                 # 触发时参考的行情价：BUY 用 best_ask，SELL 用 best_bid
    target_price: Optional[float] = None  # SELL 时为 entry * (1 + profit_ratio)
    extra: Dict[str, Any] = field(default_factory=dict)


class VolArbStrategy:
    """
    极简策略状态机（单 token）——严格“确认后换态”版：
      - FLAT → 当 best_ask <= buy_price_threshold 时，发出 BUY；
      - LONG → 当 best_bid >= entry_price * (1 + profit_ratio) 时，发出 SELL。

    注：
      * 本策略不处理 size/精度/下单，只产生信号，由上游执行。
      * 发出 BUY/SELL 信号后进入“待确认”状态，必须由上游在成交后调用
        on_buy_filled / on_sell_filled 才会推进状态机；on_reject() 解除待确认。
    """

    def __init__(self, config: StrategyConfig):
        self.cfg = config
        self._state: str = "FLAT"  # or "LONG"
        self._entry_price: Optional[float] = None
        self._awaiting: Optional[ActionType] = None  # BUY/SELL
        self._last_signal: Optional[ActionType] = None
        self._position_size: Optional[float] = None

    # ------------------------ 上游主调用：每笔行情快照 ------------------------
    def on_tick(
        self,
        best_ask: float,
        best_bid: float,
        ts: Optional[float] = None,
    ) -> Optional[Action]:
        """
        上游每次行情推送调用。返回 Action（BUY/SELL）或 None（无动作）。
        """
        # 价域守门（如不需要可在 cfg 设置为 None）
        if self.cfg.min_price is not None and (best_ask < self.cfg.min_price or best_bid < self.cfg.min_price):
            return None
        if self.cfg.max_price is not None and (best_ask > self.cfg.max_price or best_bid > self.cfg.max_price):
            return None

        if self._state == "FLAT":
            return self._maybe_buy(best_ask, ts)

        elif self._state == "LONG":
            return self._maybe_sell(best_bid, ts)

        return None

    # ------------------------ 买入/卖出触发判定 ------------------------
    def _maybe_buy(self, best_ask: float, ts: Optional[float]) -> Optional[Action]:
        if self._awaiting == ActionType.BUY and self.cfg.disable_duplicate_signal:
            return None  # 等待上游确认，不重复发 BUY

        if best_ask <= self.cfg.buy_price_threshold:
            act = Action(
                action=ActionType.BUY,
                token_id=self.cfg.token_id,
                reason=f"best_ask({best_ask:.5f}) ≤ buy_threshold({self.cfg.buy_price_threshold:.5f})",
                ref_price=best_ask,
            )
            self._last_signal = ActionType.BUY
            self._awaiting = ActionType.BUY  # 必须等待上游 on_buy_filled() 确认
            return act
        return None

    def _maybe_sell(self, best_bid: float, ts: Optional[float]) -> Optional[Action]:
        if self._entry_price is None:
            return None  # 防守式检查

        if self._awaiting == ActionType.SELL and self.cfg.disable_duplicate_signal:
            return None  # 等待上游确认，不重复发 SELL

        target = self._entry_price * (1.0 + self.cfg.profit_ratio)
        if best_bid >= target:
            act = Action(
                action=ActionType.SELL,
                token_id=self.cfg.token_id,
                reason=f"best_bid({best_bid:.5f}) ≥ target({target:.5f}) = entry({self._entry_price:.5f}) * (1+{self.cfg.profit_ratio:.4f})",
                ref_price=best_bid,
                target_price=target,
            )
            self._last_signal = ActionType.SELL
            self._awaiting = ActionType.SELL  # 必须等待上游 on_sell_filled() 确认
            return act
        return None

    # ------------------------ 上游回调：成交/被拒 ------------------------
    def on_buy_filled(self, avg_price: float, size: Optional[float] = None) -> None:
        """上游在实际买入成交后回调。"""
        if self._awaiting == ActionType.BUY:
            self._state = "LONG"
            self._entry_price = avg_price
            if size is not None:
                self._position_size = float(size)
        self._awaiting = None

    def on_sell_filled(self) -> None:
        """上游在实际卖出成交后回调。"""
        if self._awaiting == ActionType.SELL:
            self._state = "FLAT"
            self._entry_price = None
            self._position_size = None
            self._awaiting = None

    def on_reject(self, reason: Optional[str] = None) -> None:
        """上游在下单失败/被拒绝时回调，解除“待确认”以便重新发信号。"""
        self._awaiting = None

    # ------------------------ 实用方法 ------------------------
    def update_params(self, *, buy_price_threshold: Optional[float] = None, profit_ratio: Optional[float] = None) -> None:
        if buy_price_threshold is not None:
            self.cfg.buy_price_threshold = buy_price_threshold
        if profit_ratio is not None:
            self.cfg.profit_ratio = profit_ratio

    def sell_trigger_price(self) -> Optional[float]:
        if self._entry_price is None:
            return None
        return self._entry_price * (1.0 + self.cfg.profit_ratio)

    def status(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "awaiting": self._awaiting,
            "entry_price": self._entry_price,
            "sell_trigger": self.sell_trigger_price(),
            "position_size": self._position_size,
            "last_signal": self._last_signal,
            "config": {
                "token_id": self.cfg.token_id,
                "buy_price_threshold": self.cfg.buy_price_threshold,
                "profit_ratio": self.cfg.profit_ratio,
                "price_band": (self.cfg.min_price, self.cfg.max_price),
                "disable_duplicate_signal": self.cfg.disable_duplicate_signal,
            },
        }
