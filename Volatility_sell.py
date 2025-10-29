
# Volatility_sell.py
# -*- coding: utf-8 -*-
"""
批量交易辅助模块：

1) `run_batch_buy`：
   - 从 `Volatility_fliter.get_filtered_markets` 获取目标市场；
   - 逐一检查账户可用余额，余额 < 5 USDC 时暂停轮询；
   - 按 20 秒节奏调用 `Volatility_buy.execute_auto_buy` 完成批量买入；
   - 支持在运行时输入/覆盖盈利阈值，并返回给后续卖出流程。

2) `execute_auto_sell`：
   - 维持原有的五档让利 FOK 卖单执行器，职责单一。

3) `monitor_profit_and_sell`：
   - 参考 `auto_sell_pnl.py` 整合盈亏监控、卖出与到期 claim。

模块对外暴露：`run_batch_buy`、`execute_auto_sell` 与 `monitor_profit_and_sell`。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Iterable, List, Tuple

import requests

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import SELL

from Volatility_buy import execute_auto_buy
from Volatility_fliter import get_filtered_markets

__all__ = [
    "run_batch_buy",
    "execute_auto_sell",
    "monitor_profit_and_sell",
]


@dataclass
class MarketCandidate:
    token_id: str
    best_ask: float
    order_min_size: float
    info: Dict[str, Any]


@dataclass
class BuyOrderResult:
    token_id: str
    request_price: float
    size_hint: float
    response: Optional[Dict[str, Any]]
    success: bool
    market: Dict[str, Any]
    started_at: float

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["market"] = dict(self.market)
        if self.response is not None:
            payload["response"] = dict(self.response)
        return payload


@dataclass
class PositionSnapshot:
    token_id: str
    size: float
    average_price: Optional[float]
    percent_pnl: Optional[float]
    market: Dict[str, Any]
    raw: Dict[str, Any]

    @property
    def pnl_ratio(self) -> float:
        value = _parse_float(self.percent_pnl, default=0.0)
        if value is None:
            return 0.0
        if value > 1.0:
            return float(value) / 100.0
        return float(value)

    @property
    def pnl_percent(self) -> float:
        return float(self.pnl_ratio * 100.0)

    @property
    def size_real(self) -> float:
        return max(0.0, float(self.size))

    def is_profitable(self, threshold: float) -> bool:
        if self.size_real <= 0:
            return False
        return self.pnl_ratio >= float(threshold)

    def is_claimable(self) -> bool:
        raw = self.raw or {}
        flags = [
            raw.get("claimable"),
            raw.get("isClaimable"),
            raw.get("claimStatus"),
            raw.get("resolved"),
            raw.get("isResolved"),
            raw.get("status") == "RESOLVED",
        ]
        return any(bool(flag) for flag in flags)


def _parse_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _coerce_order_min_size(raw: Any) -> float:
    hint = _parse_float(raw, default=None)
    if hint is None or hint <= 0:
        return 1.0
    return float(hint)


def _resolve_candidate(market: Dict[str, Any]) -> Optional[MarketCandidate]:
    token_id = market.get("yes_token_id") or market.get("token_id")
    if not token_id:
        return None
    best_ask = _parse_float(market.get("bestAsk"))
    if best_ask is None or best_ask <= 0:
        best_ask = _parse_float(market.get("yes_price"))
    if best_ask is None or best_ask <= 0:
        return None
    order_min_size = _coerce_order_min_size(market.get("orderMinSize"))
    return MarketCandidate(
        token_id=str(token_id),
        best_ask=float(best_ask),
        order_min_size=order_min_size,
        info=market,
    )


def _extract_usdc_from_mapping(entry: Dict[str, Any]) -> Optional[float]:
    if not isinstance(entry, dict):
        return None

    id_fields = ("token", "symbol", "ticker", "currency", "asset", "name")
    label = None
    for field in id_fields:
        if field in entry:
            field_value = str(entry.get(field) or "").upper()
            if "USDC" in field_value:
                label = field_value
                break

    amount_keys = ("available", "free", "balance", "amount", "total", "value")

    if label is not None:
        for key in amount_keys:
            if key in entry:
                return _parse_float(entry.get(key), default=None)

    # 回退：字典直接包含 USDC 关键字
    for key, val in entry.items():
        if isinstance(key, str) and "usdc" in key.lower():
            return _parse_float(val, default=None)

    return None


def _extract_available_usdc(snapshot: Any) -> Optional[float]:
    if snapshot is None:
        return None
    if isinstance(snapshot, (int, float)):
        return float(snapshot)
    if isinstance(snapshot, str):
        return _parse_float(snapshot, default=None)
    if isinstance(snapshot, list):
        for item in snapshot:
            if isinstance(item, dict):
                value = _extract_usdc_from_mapping(item)
                if value is not None:
                    return value
            else:
                value = _extract_available_usdc(item)
                if value is not None:
                    return value
        return None
    if isinstance(snapshot, dict):
        # 直接命中的情形：{"USDC": {...}}
        for key, val in snapshot.items():
            if isinstance(key, str) and "usdc" in key.lower():
                value = _extract_available_usdc(val)
                if value is not None:
                    return value
        # 若无直接 key，仅在小字典里查 amount 字段
        return _extract_usdc_from_mapping(snapshot)
    return None


def _get_available_balance(client: ClobClient) -> Optional[float]:
    for method in ("get_balances", "get_balance", "get_portfolio"):
        if hasattr(client, method):
            try:
                snapshot = getattr(client, method)()
            except Exception:
                continue
            value = _extract_available_usdc(snapshot)
            if value is not None:
                return value
    return None


def _resolve_profit_threshold(
    profit_threshold: Optional[float],
    default_profit_threshold: float,
) -> float:
    if profit_threshold is not None:
        try:
            ratio = float(profit_threshold)
        except (TypeError, ValueError):
            ratio = default_profit_threshold
    else:
        try:
            raw = input(
                "请输入盈利阈值（百分比，例如 5 表示 5%），直接回车使用默认值："
            ).strip()
        except EOFError:
            raw = ""
        if raw:
            try:
                ratio = float(raw)
            except ValueError:
                ratio = default_profit_threshold
        else:
            ratio = default_profit_threshold

    if ratio > 1:
        ratio = ratio / 100.0
    if ratio <= 0:
        ratio = default_profit_threshold
    return float(ratio)


def _guess_wallet_address(client: ClobClient) -> Optional[str]:
    for attr in (
        "wallet_address",
        "walletAddress",
        "address",
        "account_address",
        "default_address",
        "defaultAddress",
        "funder",
        "proxy_address",
        "proxyAddress",
    ):
        if hasattr(client, attr):
            value = getattr(client, attr)
            if isinstance(value, str) and value:
                return value
    return None


def _normalize_wallet_address(address: Optional[str]) -> Optional[str]:
    if not address:
        return None
    text = str(address).strip()
    if not text:
        return None
    if text.startswith("0x"):
        return text
    return text


def _fetch_positions(
    wallet_address: str,
    *,
    limit: int = 200,
    size_threshold: float = 0.01,
) -> List[Dict[str, Any]]:
    url = "https://data-api.polymarket.com/positions"
    params = {
        "user": wallet_address,
        "limit": int(max(1, limit)),
        "sizeThreshold": float(max(0.0, size_threshold)),
        "sortBy": "PERCENTPNL",
        "sortDirection": "DESC",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
    except Exception as exc:
        print(f"[WARN] 获取持仓失败：{exc!r}")
    return []


def _build_position_snapshot(entry: Dict[str, Any]) -> Optional[PositionSnapshot]:
    if not isinstance(entry, dict):
        return None
    token_id = entry.get("asset") or entry.get("tokenId") or entry.get("token_id")
    if not token_id:
        return None
    size = _parse_float(entry.get("size"), default=0.0) or 0.0
    avg_price = _parse_float(entry.get("averagePrice"), default=None)
    percent_pnl = entry.get("percentPnl") or entry.get("pnlPercent")
    market = entry.get("market") or entry.get("info") or {}
    if not isinstance(market, dict):
        market = {}
    return PositionSnapshot(
        token_id=str(token_id),
        size=float(size),
        average_price=avg_price,
        percent_pnl=_parse_float(percent_pnl, default=None),
        market=market,
        raw=dict(entry),
    )


def _collect_position_snapshots(entries: List[Dict[str, Any]]) -> List[PositionSnapshot]:
    snapshots: List[PositionSnapshot] = []
    for entry in entries:
        snap = _build_position_snapshot(entry)
        if snap is not None:
            snapshots.append(snap)
    return snapshots


def _fetch_best_bid(client: ClobClient, token_id: str) -> Optional[float]:
    if hasattr(client, "get_price"):
        try:
            resp = client.get_price(token_id=str(token_id), side="buy")
            if isinstance(resp, dict):
                price = resp.get("price")
                value = _parse_float(price, default=None)
                if value is not None:
                    return float(value)
        except Exception:
            pass
    # 回退：尝试公开行情接口
    url = f"https://clob.polymarket.com/prices/{token_id}/buy"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        price = None
        if isinstance(data, dict):
            price = data.get("price") or data.get("value")
        elif isinstance(data, list) and data:
            price = data[0]
        value = _parse_float(price, default=None)
        if value is not None:
            return float(value)
    except Exception:
        pass
    return None


def _try_claim_position(client: ClobClient, token_id: str) -> Optional[Any]:
    methods = [
        "claim_positions",
        "claim_position",
        "claim_market",
        "claim",
        "claimPositions",
    ]
    last_exc: Optional[BaseException] = None
    for name in methods:
        if not hasattr(client, name):
            continue
        handler = getattr(client, name)
        for payload in ([token_id], token_id):
            try:
                result = handler(payload)
                print(f"[TRADE][CLAIM] token_id={token_id} method={name} payload={payload}")
                return result
            except TypeError:
                continue
            except Exception as exc:  # pragma: no cover - 仅记录错误
                last_exc = exc
                print(f"[WARN] claim 方法 {name} 调用失败：{exc!r}")
                break
    if last_exc is not None:
        print(f"[WARN] 未能完成 token_id={token_id} 的 claim：{last_exc!r}")
    else:
        print(f"[WARN] 当前 client 不支持自动 claim（token_id={token_id}）。")
    return None


def run_batch_buy(
    client: ClobClient,
    *,
    profit_threshold: Optional[float] = None,
    default_profit_threshold: float = 0.05,
    min_usdc_balance: float = 5.0,
    interval_seconds: float = 20.0,
    size_hint: float = 1.0,
) -> Dict[str, Any]:
    """执行批量买入流程并返回订单摘要。"""

    profit_ratio = _resolve_profit_threshold(
        profit_threshold, default_profit_threshold
    )
    print(
        f"[INIT] 批量买入启动，盈利阈值设定为 {profit_ratio * 100:.2f}% (ratio={profit_ratio:.4f})"
    )

    print("[INIT] 调用 Volatility_fliter.get_filtered_markets() 获取候选市场…")
    markets = get_filtered_markets()
    print(f"[INIT] 共获取 {len(markets)} 个候选市场。")

    candidates: List[MarketCandidate] = []
    for market in markets:
        candidate = _resolve_candidate(market)
        if candidate is None:
            print("[WARN] 跳过缺少 token_id 或报价的市场：", market.get("question"))
            continue
        candidates.append(candidate)

    if not candidates:
        print("[WARN] 没有可用于买入的市场，流程结束。")
        return {"profit_threshold": profit_ratio, "orders": [], "markets": []}

    executed: List[BuyOrderResult] = []

    for index, candidate in enumerate(candidates, start=1):
        print(
            f"[RUN] 第 {index}/{len(candidates)} 个市场：{candidate.info.get('question', '(unknown)')}"
        )

        while True:
            balance = _get_available_balance(client)
            if balance is None:
                print("[WARN] 无法获取账户余额，默认继续执行买入。")
                break
            if balance >= min_usdc_balance:
                print(f"[INIT] 可用余额 {balance:.2f} USDC，满足买入条件。")
                break
            print(
                f"[HINT] 可用余额 {balance:.2f} USDC 低于阈值 {min_usdc_balance}，暂停 {interval_seconds} 秒后重试。"
            )
            time.sleep(max(1.0, float(interval_seconds)))

        price = candidate.best_ask
        size = max(size_hint, candidate.order_min_size)
        started_at = time.time()
        response: Optional[Dict[str, Any]] = None
        success = False
        try:
            response = execute_auto_buy(client, candidate.token_id, price, size)
            status = str((response or {}).get("status", "")).lower()
            success = status in {"success", "matched"}
            print(
                f"[TRADE][BUY] token_id={candidate.token_id} price={price} size_hint={size} status={status}"
            )
        except Exception as exc:
            print(f"[ERR] 买入失败：token_id={candidate.token_id} error={exc!r}")

        executed.append(
            BuyOrderResult(
                token_id=candidate.token_id,
                request_price=price,
                size_hint=size,
                response=response,
                success=success,
                market=candidate.info,
                started_at=started_at,
            )
        )

        if index < len(candidates):
            print(
                f"[RUN] 等待 {interval_seconds} 秒后尝试下一个市场…"
            )
            time.sleep(max(0.0, float(interval_seconds)))

    print("[DONE] 批量买入流程结束。")
    return {
        "profit_threshold": profit_ratio,
        "orders": [item.to_dict() for item in executed],
        "markets": [candidate.info for candidate in candidates],
    }


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


def _format_market_label(market: Dict[str, Any]) -> str:
    if not market:
        return "(unknown market)"
    parts = []
    title = market.get("title") or market.get("question")
    if title:
        parts.append(str(title))
    outcome = market.get("outcome") or market.get("side")
    if outcome:
        parts.append(str(outcome))
    return " | ".join(parts) if parts else str(market)


def _log_iteration_header(iteration: int, ratio: float) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(
        f"[RUN] 盈利监控轮次 {iteration} @ {now} (threshold={ratio * 100:.2f}%)"
    )


def _inspect_positions(
    entries: List[PositionSnapshot],
    *,
    profit_ratio: float,
) -> Tuple[List[PositionSnapshot], List[PositionSnapshot]]:
    profitable: List[PositionSnapshot] = []
    claimables: List[PositionSnapshot] = []
    for snap in entries:
        label = _format_market_label(snap.market)
        print(
            f"[PX] token_id={snap.token_id} size={snap.size_real:.4f} avg={snap.average_price} pnl={snap.pnl_percent:.2f}% -> {label}"
        )
        if snap.is_profitable(profit_ratio):
            profitable.append(snap)
        elif snap.is_claimable():
            claimables.append(snap)
    return profitable, claimables


def _sell_positions(
    client: ClobClient,
    positions: List[PositionSnapshot],
) -> List[Dict[str, Any]]:
    responses: List[Dict[str, Any]] = []
    for snap in positions:
        best_bid = _fetch_best_bid(client, snap.token_id)
        if best_bid is None:
            print(
                f"[WARN] 无法获取 token_id={snap.token_id} 的买一价，跳过卖出。"
            )
            continue
        try:
            resp = execute_auto_sell(client, snap.token_id, best_bid, snap.size_real)
            if resp is not None:
                responses.append(resp)
        except Exception as exc:
            print(
                f"[ERR] 卖出失败 token_id={snap.token_id} bid={best_bid}: {exc!r}"
            )
    return responses


def _claim_positions(client: ClobClient, claimables: List[PositionSnapshot]) -> None:
    for snap in claimables:
        print(
            f"[HINT] token_id={snap.token_id} 市场已到期，尝试自动 claim。"
        )
        _try_claim_position(client, snap.token_id)


def monitor_profit_and_sell(
    client: ClobClient,
    *,
    profit_threshold: Optional[float] = None,
    default_profit_threshold: float = 0.05,
    check_interval: float = 600.0,
    min_usdc_balance: float = 5.0,
    wallet_address: Optional[str] = None,
    max_cycles: Optional[int] = None,
) -> Dict[str, Any]:
    """每 10 分钟检查盈亏并在达标时执行卖出/claim。"""

    profit_ratio = _resolve_profit_threshold(
        profit_threshold, default_profit_threshold
    )
    wallet = _normalize_wallet_address(
        wallet_address or _guess_wallet_address(client)
    )
    if not wallet:
        print("[ERR] 无法确定钱包地址，终止盈利监控流程。")
        return {
            "profit_threshold": profit_ratio,
            "iterations": [],
            "resume_buy": False,
            "wallet": wallet,
        }

    print(
        f"[INIT] 盈利监控启动：wallet={wallet} threshold={profit_ratio * 100:.2f}%"
    )

    iteration = 0
    summary: Dict[str, Any] = {
        "profit_threshold": profit_ratio,
        "iterations": [],
        "resume_buy": False,
        "wallet": wallet,
    }

    while True:
        iteration += 1
        _log_iteration_header(iteration, profit_ratio)

        raw_positions = _fetch_positions(wallet)
        snapshots = _collect_position_snapshots(raw_positions)

        if not snapshots:
            print("[RUN] 当前无持仓记录。")
        profitable, claimables = _inspect_positions(
            snapshots, profit_ratio=profit_ratio
        )

        iteration_result: Dict[str, Any] = {
            "profitable": [snap.token_id for snap in profitable],
            "claimables": [snap.token_id for snap in claimables],
            "sells": [],
        }

        if profitable:
            print(f"[RUN] 检测到 {len(profitable)} 个盈利仓位，开始卖出流程…")
            sell_responses = _sell_positions(client, profitable)
            iteration_result["sells"] = sell_responses
        else:
            print("[RUN] 暂无达到盈利阈值的仓位。")

        if claimables:
            _claim_positions(client, claimables)

        summary["iterations"].append(iteration_result)

        if iteration_result["sells"]:
            balance = _get_available_balance(client)
            if balance is None:
                print(
                    "[WARN] 卖出后无法获取余额，默认视为可继续买入。"
                )
                summary["resume_buy"] = True
                break
            if balance >= float(min_usdc_balance):
                print(
                    f"[DONE] 卖出完成且余额 {balance:.2f} USDC ≥ {min_usdc_balance}，可重新启动买入流程。"
                )
                summary["resume_buy"] = True
                break
            print(
                f"[HINT] 卖出完成但余额 {balance:.2f} USDC 低于阈值 {min_usdc_balance}，继续监控。"
            )

        if max_cycles is not None and iteration >= max_cycles:
            print(
                f"[DONE] 达到最大轮次 {max_cycles}，结束盈利监控。"
            )
            break

        print(
            f"[RUN] 休眠 {check_interval} 秒后继续下一轮盈利监控…"
        )
        time.sleep(max(1.0, float(check_interval)))

    return summary
