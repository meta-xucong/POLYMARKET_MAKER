from Volatility_arbitrage_strategy import StrategyConfig, VolArbStrategy


def _build_strategy() -> VolArbStrategy:
    return VolArbStrategy(StrategyConfig(token_id="T"))


def test_drop_stats_reset_after_full_sell():
    strategy = _build_strategy()

    strategy.on_tick(best_ask=0.52, best_bid=0.50, ts=0.0)
    strategy.on_tick(best_ask=0.54, best_bid=0.52, ts=1.0)

    strategy.on_buy_filled(avg_price=0.52, size=1.0)

    status_before = strategy.status()
    assert status_before["price_history_len"] > 0
    assert status_before["drop_stats"]["window_high"] is not None

    strategy.on_sell_filled(avg_price=0.55, size=1.0, remaining=0.0)

    status_after = strategy.status()
    assert status_after["state"] == "FLAT"
    assert status_after["price_history_len"] == 0
    assert status_after["drop_stats"]["window_high"] is None
    assert status_after["drop_stats"]["max_drop_ratio"] is None


def test_drop_stats_reset_when_syncing_flat_position():
    strategy = _build_strategy()

    strategy.on_tick(best_ask=0.6, best_bid=0.58, ts=0.0)
    strategy.on_buy_filled(avg_price=0.58, size=2.0)

    assert strategy.status()["price_history_len"] > 0

    strategy.sync_position(total_position=0.0)

    status_after = strategy.status()
    assert status_after["state"] == "FLAT"
    assert status_after["price_history_len"] == 0
    assert status_after["drop_stats"]["window_high"] is None
    assert status_after["drop_stats"]["current_drop_ratio"] is None
