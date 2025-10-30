# -*- coding: utf-8 -*-
"""Volatility_arbitrage_run
============================

统一入口脚本：串联初始化、连通性自检、批量买入与盈利监控。

整体流程：
1. 检查 REST / WS 连通性并初始化 `ClobClient`；
2. 交互式收集盈利阈值、资金阈值及节奏配置；
3. 按照 `Volatility_fliter` 的筛选结果执行批量买入；
4. 启动盈利监控与 FOK 五档卖出流程；
5. 根据资金状况决定是否继续下一轮循环。

该脚本不直接下单，所有交易均委托 `Volatility_buy.execute_auto_buy`
与 `Volatility_sell.execute_auto_sell`。
"""

from __future__ import annotations

from typing import Dict, Any, Optional

from Volatility_arbitrage_main_rest import (
    get_client,
    verify_rest_capabilities,
    RestConnectivityError,
    RestConfigurationError,
)
from Volatility_arbitrage_main_ws import (
    verify_ws_connection,
    WsConnectivityError,
)
from Volatility_sell import run_batch_buy, monitor_profit_and_sell
from Volatility_config import (
    TRADE_CONFIG,
    config_snapshot,
    log_event,
)

# === 默认配置 ===
DEFAULT_PROFIT_PERCENT = TRADE_CONFIG.default_profit_percent        # 盈利阈值（百分比）
DEFAULT_MIN_USDC_BALANCE = TRADE_CONFIG.min_usdc_balance            # 最小可用余额阈值（USDC）
DEFAULT_BUY_INTERVAL = TRADE_CONFIG.buy_interval_seconds           # 批量买入节奏（秒）
DEFAULT_CHECK_INTERVAL = TRADE_CONFIG.check_interval_seconds       # 盈利监控间隔（秒）


# === 辅助函数 ===
def _prompt_float(prompt: str, default: float, *, min_value: Optional[float] = None) -> float:
    while True:
        try:
            raw = input(prompt).strip()
        except EOFError:
            raw = ""
        if not raw:
            value = float(default)
        else:
            try:
                value = float(raw)
            except ValueError:
                log_event("WARN", "输入非法，请重新输入数字。")
                continue
        if min_value is not None and value < min_value:
            log_event("WARN", f"数值需 ≥ {min_value}，请重新输入。")
            continue
        return float(value)


def _prompt_profit_ratio(default_percent: float) -> float:
    default_prompt = f"（默认 {default_percent} 表示 {default_percent}%）"
    prompt = f"请输入盈利阈值百分比{default_prompt}："
    percent = _prompt_float(prompt, default_percent, min_value=0.01)
    ratio = percent / 100.0 if percent > 1 else percent
    if ratio <= 0:
        return max(default_percent / 100.0, 0.0001)
    return float(ratio)


def _format_rest_snapshot(snapshot: Dict[str, Any]) -> str:
    host = snapshot.get("host", "?")
    status = snapshot.get("status_code")
    balance_method = snapshot.get("balance_method") or "(unknown)"
    price_method = snapshot.get("price_method") or "(unknown)"
    env = snapshot.get("env", {})
    env_hint = f"key={env.get('key_hint', '??')} funder={env.get('funder', '??')}"
    return (
        f"host={host} status={status} balance_method={balance_method} "
        f"price_method={price_method} env={env_hint}"
    )


def _format_orders_summary(summary: Dict[str, Any]) -> str:
    orders = summary.get("orders") or []
    total = len(orders)
    success = sum(1 for item in orders if item.get("success"))
    return f"共 {total} 单（成功 {success} 单）"


def main() -> None:
    defaults = config_snapshot()["trade"]
    log_event("INIT", "Polymarket 套利程序启动。", context=defaults)

    # --- REST 连通性检查 ---
    try:
        rest_snapshot = verify_rest_capabilities()
    except RestConfigurationError as exc:
        log_event("ERR", f"REST 配置错误：{exc}")
        return
    except RestConnectivityError as exc:
        log_event("ERR", f"REST 连通性检查失败：{exc}")
        return
    else:
        log_event(
            "INIT",
            "REST 连通性通过。",
            context={"summary": _format_rest_snapshot(rest_snapshot)},
        )

    # --- WS 连通性检查 ---
    try:
        ws_snapshot = verify_ws_connection()
    except WsConnectivityError as exc:
        log_event("ERR", f"WS 连通性检查失败：{exc}")
        return
    else:
        log_event(
            "INIT",
            "WS 连通性通过。",
            context={
                "url": ws_snapshot.get("url"),
                "attempts": ws_snapshot.get("attempts"),
            },
        )

    # --- 初始化客户端 ---
    try:
        client = get_client()
    except RestConfigurationError as exc:
        log_event("ERR", f"初始化 REST 客户端失败：{exc}")
        return
    except Exception as exc:  # pragma: no cover - 防御性兜底
        log_event("ERR", f"初始化 REST 客户端出现异常：{exc}")
        return
    log_event("INIT", "ClobClient 就绪，可执行买卖。")

    # --- 交互式配置 ---
    profit_ratio = _prompt_profit_ratio(DEFAULT_PROFIT_PERCENT)
    min_balance = _prompt_float(
        "请输入最小可用余额阈值（USDC，默认 5）：",
        DEFAULT_MIN_USDC_BALANCE,
        min_value=0.0,
    )
    buy_interval = _prompt_float(
        "请输入批量买入间隔秒数（默认 20）：",
        DEFAULT_BUY_INTERVAL,
        min_value=1.0,
    )
    check_interval = _prompt_float(
        "请输入盈利检查间隔秒数（默认 600）：",
        DEFAULT_CHECK_INTERVAL,
        min_value=30.0,
    )

    log_event(
        "CHOICE",
        "配置摘要。",
        context={
            "profit_percent": f"{profit_ratio * 100:.2f}%",
            "min_balance": f"{min_balance}USDC",
            "buy_interval": f"{buy_interval}s",
            "check_interval": f"{check_interval}s",
        },
    )

    cycle = 0
    while True:
        cycle += 1
        log_event("RUN", f"===== 周期 {cycle}：批量买入阶段 =====")
        buy_summary = run_batch_buy(
            client,
            profit_threshold=profit_ratio,
            default_profit_threshold=profit_ratio,
            min_usdc_balance=min_balance,
            interval_seconds=buy_interval,
        )
        log_event("RUN", f"批量买入完成：{_format_orders_summary(buy_summary)}")

        log_event("RUN", f"===== 周期 {cycle}：盈利监控阶段 =====")
        monitor_summary = monitor_profit_and_sell(
            client,
            profit_threshold=profit_ratio,
            default_profit_threshold=profit_ratio,
            check_interval=check_interval,
            min_usdc_balance=min_balance,
        )
        iterations = monitor_summary.get("iterations") or []
        log_event("RUN", f"盈利监控结束，本轮共执行 {len(iterations)} 次检查。")

        if not monitor_summary.get("resume_buy"):
            log_event("DONE", "盈利监控未建议继续买入，程序结束。")
            break

        try:
            cont = input("是否继续下一轮？(Y/n)：").strip().lower()
        except EOFError:
            cont = "y"
        if cont and cont.startswith("n"):
            log_event("DONE", "用户选择终止，程序结束。")
            break

        log_event("RUN", "准备进入下一轮……")


if __name__ == "__main__":
    main()
