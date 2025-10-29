# Volatility_arbitrage_main.py
# -*- coding: utf-8 -*-
"""
Polymarket CLOB API 接入主模块（最小版）

用途：被其它模块（价格查询/买入/卖出）导入复用，提供一个已完成鉴权的 ClobClient 实例。

环境变量（必须）：
- POLY_KEY          : 私钥（hex 字符串，0x 前缀可选）
- POLY_FUNDER       : Proxy Wallet / Deposit Address（你的充值地址）

环境变量（可选，带默认）：
- POLY_HOST         : 默认 https://clob.polymarket.com
- POLY_CHAIN_ID     : 默认 137（Polygon）
- POLY_SIGNATURE    : 默认 2（EIP-712）

用法：
>>> from Volatility_arbitrage_main import get_client
>>> client = get_client()
>>> # 之后在任意模块里复用 client 即可下单/询价
"""
import os
import time
from typing import Any, Dict, Optional

try:
    from py_clob_client.client import ClobClient
except ModuleNotFoundError as exc:
    raise RuntimeError("缺少依赖 py-clob-client，请先执行: pip install py-clob-client") from exc

try:
    import requests
except ModuleNotFoundError:
    requests = None  # 仅在 verify_rest_capabilities 中使用

# ---- 默认配置 ----
DEFAULT_HOST = "https://clob.polymarket.com"
DEFAULT_CHAIN_ID = 137
DEFAULT_SIGNATURE_TYPE = 2

__all__ = [
    "init_client",
    "get_client",
    "verify_rest_capabilities",
    "validate_rest_environment",
    "RestConnectivityError",
    "RestConfigurationError",
]

_CLIENT_SINGLETON = None  # 模块级单例


class RestConnectivityError(RuntimeError):
    """REST 连接或基础功能校验失败时抛出的统一异常。"""


class RestConfigurationError(RuntimeError):
    """环境变量配置不符合预期时抛出的统一异常。"""


def validate_rest_environment() -> Dict[str, str]:
    """确保用于初始化 REST 客户端的关键环境变量存在且格式正确。"""
    try:
        raw_key = os.environ["POLY_KEY"].strip()
    except KeyError as exc:
        raise RestConfigurationError("未设置必需的环境变量 POLY_KEY") from exc

    try:
        raw_funder = os.environ["POLY_FUNDER"].strip()
    except KeyError as exc:
        raise RestConfigurationError("未设置必需的环境变量 POLY_FUNDER") from exc

    if not raw_key:
        raise RestConfigurationError("POLY_KEY 为空，请填写有效的私钥。")
    normalized_key = _normalize_privkey(raw_key)
    if len(normalized_key) != 64:
        raise RestConfigurationError("POLY_KEY 长度异常，应为 32 字节（64 个 hex 字符）。")
    try:
        int(normalized_key, 16)
    except ValueError as exc:
        raise RestConfigurationError("POLY_KEY 不是合法的十六进制字符串。") from exc

    if not raw_funder:
        raise RestConfigurationError("POLY_FUNDER 为空，请填写有效的钱包地址。")
    if not raw_funder.startswith("0x") or len(raw_funder) != 42:
        raise RestConfigurationError("POLY_FUNDER 应为 0x 开头的 42 位地址。")

    return {"key": raw_key, "normalized_key": normalized_key, "funder": raw_funder}


def _normalize_privkey(k: str) -> str:
    # 允许传入带/不带 0x 的 hex；统一去掉 0x 前缀
    return k[2:] if k.startswith(("0x", "0X")) else k


def init_client() -> ClobClient:
    host = os.getenv("POLY_HOST", DEFAULT_HOST)
    chain_id = int(os.getenv("POLY_CHAIN_ID", str(DEFAULT_CHAIN_ID)))
    signature_type = int(os.getenv("POLY_SIGNATURE", str(DEFAULT_SIGNATURE_TYPE)))

    env = validate_rest_environment()
    key = env["normalized_key"]
    funder = env["funder"]

    client = ClobClient(
        host,
        key=key,
        chain_id=chain_id,
        signature_type=signature_type,
        funder=funder,
    )
    # 生成并设置 API 凭证（基于私钥派生）
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


def get_client() -> ClobClient:
    """获取（或懒加载）单例客户端。"""
    global _CLIENT_SINGLETON
    if _CLIENT_SINGLETON is None:
        _CLIENT_SINGLETON = init_client()
    return _CLIENT_SINGLETON


def verify_rest_capabilities(client: Optional[ClobClient] = None,
                             attempts: int = 3,
                             request_timeout: float = 5.0,
                             retry_delay: float = 1.5) -> Dict[str, Any]:
    """确认 REST Host 可达，且客户端具备买/卖所需的基础能力。"""
    if requests is None:
        raise RestConnectivityError("缺少 requests 依赖，无法执行 REST 连通性检测。请先安装 requests。")

    env_snapshot = validate_rest_environment()
    env_public = {
        "key_hint": env_snapshot["normalized_key"][:6] + "..." + env_snapshot["normalized_key"][-4:],
        "funder": env_snapshot["funder"],
    }

    host = os.getenv("POLY_HOST", DEFAULT_HOST).rstrip("/")
    if not host.startswith("http://") and not host.startswith("https://"):
        host = "https://" + host.lstrip("/")

    last_exc: Optional[BaseException] = None
    status_code: Optional[int] = None
    for attempt in range(1, int(max(1, attempts)) + 1):
        try:
            resp = requests.get(host, timeout=float(request_timeout))
            status_code = resp.status_code
            break
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                raise RestConnectivityError(f"无法访问 REST Host: {host}") from exc
            time.sleep(max(0.5, float(retry_delay)))

    cli = client or get_client()

    required_methods = ["create_order", "post_order"]
    missing = [m for m in required_methods if not hasattr(cli, m)]
    if missing:
        raise RestConnectivityError(f"ClobClient 缺少必要方法: {', '.join(missing)}")

    balance_snapshot: Optional[Any] = None
    balance_method: Optional[str] = None
    for name in ("get_balances", "get_balance", "get_portfolio"):
        if hasattr(cli, name):
            balance_method = name
            break
    if balance_method:
        try:
            balance_snapshot = getattr(cli, balance_method)()
        except Exception as exc:
            raise RestConnectivityError("无法通过 REST 客户端查询账户资产。") from exc

    # 检查行情查询能力
    price_method = None
    for name in ("get_price", "get_orderbook", "get_price_quotes"):
        if hasattr(cli, name):
            price_method = name
            break
    if price_method is None:
        raise RestConnectivityError("ClobClient 缺少行情查询相关方法（如 get_price）。")

    return {
        "host": host,
        "status_code": status_code,
        "balance_method": balance_method,
        "price_method": price_method,
        "balance_snapshot": balance_snapshot,
        "env": env_public,
    }


if __name__ == "__main__":
    # 简单自检：仅做初始化，不发起额外网络调用
    c = get_client()
    print("[INIT] ClobClient 就绪。host=%s chain_id=%s signature_type=%s funder=%s" % (
        os.getenv("POLY_HOST", DEFAULT_HOST),
        os.getenv("POLY_CHAIN_ID", str(DEFAULT_CHAIN_ID)),
        os.getenv("POLY_SIGNATURE", str(DEFAULT_SIGNATURE_TYPE)),
        os.environ.get("POLY_FUNDER", "?")[:10] + "...",
    ))
