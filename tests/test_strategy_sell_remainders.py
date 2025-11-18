import time

import pytest

from Volatility_arbitrage_strategy import ActionType, StrategyConfig, VolArbStrategy


def test_on_sell_filled_treats_dust_as_flat():
    cfg = StrategyConfig(token_id="T", min_market_order_size=5.0)
    strategy = VolArbStrategy(cfg)

    strategy.on_buy_filled(avg_price=0.5, size=10.0)
    strategy.on_sell_filled(avg_price=0.55, size=9.0, remaining=4.0)

    status = strategy.status()
    assert status["state"] == "FLAT"
    assert status["awaiting"] is None
    assert status["position_size"] is None


def test_on_sell_filled_marks_remaining_sell_pending():
    cfg = StrategyConfig(token_id="T", min_market_order_size=5.0)
    strategy = VolArbStrategy(cfg)

    strategy.on_buy_filled(avg_price=0.5, size=10.0)
    strategy.on_sell_filled(avg_price=0.6, size=4.0, remaining=6.0)

    status = strategy.status()
    assert status["state"] == "LONG"
    assert status["awaiting"] == ActionType.SELL
    assert status["position_size"] == pytest.approx(6.0)


def test_on_tick_reconciles_dust_position():
    cfg = StrategyConfig(token_id="T", min_market_order_size=5.0)
    strategy = VolArbStrategy(cfg)

    # 建仓
    strategy.on_buy_filled(avg_price=0.5, size=10.0)

    # 模拟外部同步到极小尾仓
    strategy._position_size = 0.2
    strategy._state = "LONG"
    strategy._awaiting = None

    # 任意行情 tick 会触发尘埃清理
    strategy.on_tick(ts=time.time(), best_ask=0.51, best_bid=0.5)

    status = strategy.status()
    assert status["state"] == "FLAT"
    assert status["awaiting"] is None
    assert status["position_size"] is None
