# Polymarket 波动率套利脚本套件

本仓库整理了一套围绕 Polymarket CLOB API 的套利脚本，涵盖从市场筛选、批量买入、盈利监控到卖出执行的完整闭环。脚本均以中文注释编写，便于在受控环境中快速搭建自动化策略原型。

## 功能概览

| 模块 | 作用 | 关键特性 |
| ---- | ---- | -------- |
| `Volatility_arbitrage_run.py` | 统一入口脚本 | 串联初始化、连通性自检、交互式参数收集、批量买入与盈利监控循环 |
| `Volatility_arbitrage_main_rest.py` | REST 客户端封装 | 读取密钥环境变量、创建 `ClobClient` 单例，并提供连通性验证 |
| `Volatility_arbitrage_main_ws.py` | WebSocket 连接器 | 提供最小订阅器与握手自检，可按 token_id 订阅盘口事件 |
| `Volatility_fliter.py` | 市场筛选器 | 基于成交量、到期时间、价格区间与黑名单词过滤可交易市场 |
| `Volatility_buy.py` | 买单执行器 | 将买单规范化为 2/4/2 精度，并以 FAK（近似 IOC）方式提交 |
| `Volatility_sell.py` | 批量买入与卖出逻辑 | 调用筛选结果批量下单，轮询盈亏、按五档 FOK 卖出并处理 claim |
| `auto_sell_pnl.py` | 盈亏辅助逻辑 | 供 `Volatility_sell.py` 参考的盈亏与卖出实现 |
| `poly_filter.py` | 旧版筛选脚本 | 作为新筛选器的回溯参考 |

## 依赖与环境准备

1. **Python 版本**：推荐 Python 3.10 及以上。
2. **基础依赖**：
   ```bash
   pip install py-clob-client websocket-client requests
   ```
   若使用 `requirements.txt`，可自行整理后一次性安装。
3. **必须的环境变量**（用于 REST 鉴权）：
   - `POLY_KEY`：私钥十六进制字符串（可含 `0x` 前缀）。
   - `POLY_FUNDER`：充值地址（Proxy Wallet）。
4. **可选环境变量**：
   - `POLY_HOST`：REST 主机地址，默认 `https://clob.polymarket.com`。
   - `POLY_CHAIN_ID`：链 ID，默认 `137`（Polygon）。
   - `POLY_SIGNATURE`：签名类型，默认 `2`（EIP-712）。

建议在运行前，通过 `Volatility_arbitrage_main_rest.py` / `Volatility_arbitrage_main_ws.py` 中的自检函数验证连通性。

## 使用方法

1. **克隆与安装依赖**
   ```bash
   git clone <repo-url>
   cd POLYMARKET3
   python -m venv .venv
   source .venv/bin/activate  # Windows 使用 .venv\Scripts\activate
   pip install py-clob-client websocket-client requests
   ```

2. **配置环境变量**
   ```bash
   export POLY_KEY="<你的私钥>"
   export POLY_FUNDER="<你的充值地址>"
   # 如需自定义：
   export POLY_HOST="https://clob.polymarket.com"
   export POLY_CHAIN_ID="137"
   export POLY_SIGNATURE="2"
   ```

3. **运行入口脚本**
   ```bash
   python Volatility_arbitrage_run.py
   ```
   程序将先验证 REST / WS 连通性，再交互式询问盈利阈值、余额阈值以及买入与监控节奏，并循环执行批量买入与盈利监控。

4. **单独使用模块（可选）**
   - **验证 REST 能力**：
     ```bash
     python Volatility_arbitrage_main_rest.py
     ```
   - **验证 WS 连接**：
     ```bash
     python Volatility_arbitrage_main_ws.py --source <市场 URL 或 token_id 列表>
     ```
   - **独立筛选市场**：
     ```bash
     python Volatility_fliter.py
     ```

## 注意事项

- 本套脚本仅作为策略原型示例，尚未经过完备测试，请先在沙盒或极小额度下验证。
- 运行前请确保账户余额充足，并了解自动化交易的潜在风险与合规要求。
- `Volatility_sell.py` 中的盈利监控与自动卖出逻辑依赖 REST 账户快照，请关注接口速率限制及异常处理。

## 常见问题

- **缺少依赖**：若提示 `ModuleNotFoundError`，请按“依赖与环境准备”章节安装所需包。
- **连通性失败**：检查本地网络、代理设置及 Polymarket API 状态，必要时调整 `POLY_HOST` 或重试。
- **环境变量错误**：`POLY_KEY` 必须是 64 位十六进制字符串（可含 `0x`），`POLY_FUNDER` 应为 `0x` 开头的 42 位地址。

准备就绪后即可按需扩展策略逻辑或接入更多监控与风控模块。祝你测试顺利！
