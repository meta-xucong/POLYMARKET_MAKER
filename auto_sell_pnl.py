# -*- coding: utf-8 -*-
# 从持仓筛选 PnL > 1.5% 的仓位，自动市价卖出（极简，使用 get_price）

import math
import requests
import json
from web3 import Web3
from eth_account import Account

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from notify_wechat import wechat_push

# ==== 配置 ====
host = "https://clob.polymarket.com"
key = "c8b8b589f2a6b1f295f99f4a0a03b4e2a416e55ea584733e252c1b297bf4ff7a"
chain_id = 137

# 你确认这是你的个人 Proxy Wallet（Deposit Address）
POLYMARKET_PROXY_ADDRESS = "0xC40CF2DfEfe86EBBA0d09ABb1Dfa5a8bF110bc64"
proxy_wallet = POLYMARKET_PROXY_ADDRESS

MIN_PNL_PCT = 5  # 触发卖出的 PnL 阈值（百分比）

# ==== 初始化 ====
w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
account = Account.from_key(key)
wallet_address = account.address

client = ClobClient(
    host,
    key=key,
    chain_id=chain_id,
    signature_type=2,
    funder=POLYMARKET_PROXY_ADDRESS,   # 你的 Proxy Wallet
)
client.set_api_creds(client.create_or_derive_api_creds())

# ==== 用 CLOB 取顶盘口价（官方 get_price）====
def get_top_of_book_price(token_id, side):
    """
    卖单(SELL) -> 取买方(bid) => side='buy'
    买单(BUY)  -> 取卖方(ask) => side='sell'
    返回 float 价格或 None
    """
    side_param = "buy" if side.upper() == "SELL" else "sell"
    r = client.get_price(token_id=token_id, side=side_param)  # {"price": ".512"}
    if not r or r.get("price") is None or r.get("price") == "":
        return None
    return float(r["price"])

# ==== 市价下单（保持你原有逻辑，仅换取价函数）====
def market_order(size, token_id, side="SELL"):
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        price = get_top_of_book_price(token_id, side)  # 改这里
        print(f"Attempt {attempt} - Original Price: {price}")
        if price is None:
            print(f"Failed to get price for token_id: {token_id}")
            return

        if attempt == 1:
            price_real = round(price, 4)
            print(f"Adjusted Price after 0% {'increase' if side == 'BUY' else 'decrease'}: {price_real}")
        else:
            if side == "BUY":
                price_real = round(price * (1 + 0.01 * (attempt - 1)), 4)  # 每次+1%
            else:
                price_real = round(price * (1 - 0.01 * (attempt - 1)), 4)  # 每次-1%
            print(f"Adjusted Price after {1 * (attempt - 1)}% {'increase' if side == 'BUY' else 'decrease'}: {price_real}")

        size_real = math.floor(size * 100) / 100.0
        if size_real < 0.01:
            print("Position too small after 2dp floor, skip.")
            return
        try:
            side_constant = SELL if side == "SELL" else BUY
            order_args = OrderArgs(price=price_real, size=size_real, side=side_constant, token_id=token_id)
            signed_order = client.create_order(order_args)
            resp = client.post_order(signed_order, OrderType.FOK)

            if resp.get("status") == "success":
                print(f"Order successfully placed at {price_real}")
                print(f"Order ID: {resp.get('orderID')}")
                print(f"Status: {resp.get('status')}")
                print(f"Taking Amount: {resp.get('takingAmount')}")
                print(f"Making Amount: {resp.get('makingAmount')}")
                print(f"Transaction Hashes: {resp.get('transactionsHashes')}")
                wechat_push("已卖出盈利订单", f"Order successfully placed at {price_real}\nOrder ID: {resp.get('orderID')}\nTaking Amount: {resp.get('takingAmount')}\nMaking Amount: {resp.get('makingAmount')}\nTransaction Hashes: {resp.get('transactionsHashes')}")
                return
            elif resp.get("status") == "matched":
                print(f"Attempt {attempt} matched, but not fully filled.")
                print(f"Order ID: {resp.get('orderID')}")
                print(f"Status: {resp.get('status')}")
                print(f"Taking Amount: {resp.get('takingAmount')}")
                print(f"Making Amount: {resp.get('makingAmount')}")
                print(f"Transaction Hashes: {resp.get('transactionsHashes')}")
                wechat_push("已卖出盈利订单",
                            f"Attempt {attempt} matched, but not fully filled.\nOrder ID: {resp.get('orderID')}\nTaking Amount: {resp.get('takingAmount')}\nMaking Amount: {resp.get('makingAmount')}\nTransaction Hashes: {resp.get('transactionsHashes')}")
                return
        except Exception as e:
            print(f"Order failed: {str(e)}")
    print("Order failed after 5 attempts with increasing/decreasing price adjustments.")

# ==== 拉持仓 -> 挑选 PnL>阈值 -> 卖出 ====
pos_url = "https://data-api.polymarket.com/positions"
params = {
    "user": proxy_wallet,       # 用你的 Proxy Wallet 地址
    "sizeThreshold": 1,
    "limit": 200,
    "sortBy": "PERCENTPNL",
    "sortDirection": "DESC",
}
positions = requests.get(pos_url, params=params, timeout=10).json()

targets = []
for p in positions:
    pnl_pct = float(p.get("percentPnl", 0) or 0)
    if pnl_pct > MIN_PNL_PCT:
        token_id = p.get("asset")                    # 直接用 asset 作为 token_id
        size = float(p.get("size", 0) or 0)
        if token_id and size > 0:
            targets.append({
                "title": p.get("title"),
                "outcome": p.get("outcome"),
                "pnl_pct": pnl_pct,
                "size": size,
                "token_id": token_id
            })

print(f"Targets to SELL (PnL > {MIN_PNL_PCT}%): {len(targets)}")
for t in targets:
    print(f"- {t['title']} | {t['outcome']} | pnl={t['pnl_pct']:.2f}% | size={t['size']} | token_id={t['token_id']}")
    market_order(t["size"], t["token_id"], side="SELL")
