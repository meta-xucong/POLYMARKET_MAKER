# Polymarket 扫尾脚本套件

## 简介
“扫尾脚本套件”围绕 Polymarket CLOB API 构建，目标是在受控环境中自动完成市场筛选、节奏化买入、盈利监控与卖出回合，帮助策略在尾盘阶段快速扫尾与回收资金。入口脚本 `Volatility_arbitrage_run.py` 负责统筹流程，其他模块按职责分层解耦。

## 核心功能
- **连通性自检**：运行前通过 REST / WebSocket 检查鉴权、订阅与报价响应，确保客户端可下单与接收盘口事件。
- **条件化筛选**：依据成交量、到期窗口、价格区间与黑名单关键词过滤候选市场，集中参数配置便于快速调整。
- **批量买入**：在余额充足时按固定节奏逐一买入符合条件的合约，并保留默认盈利阈值传递给后续流程。
- **盈利监控与卖出**：定期扫描持仓盈亏，达到阈值即触发五档 FOK 卖出，必要时执行到期 claim，卖出后根据余额决定是否进入下一轮。

## 模块与职责
| 脚本 | 角色 | 关键要点 |
| ---- | ---- | -------- |
| `Volatility_arbitrage_run.py` | 入口调度 | 启动日志、连通性检查、交互式参数输入、循环驱动买入与监控 |
| `Volatility_fliter.py` | 市场筛选 | 集中阈值配置，基于 REST API 拉取市场并过滤合格候选 |
| `Volatility_buy.py` | 买单执行 | 规范精度、构造 FAK/IOC 买单并提交至 CLOB |
| `Volatility_sell.py` | 买入协程 & 盈利监控 | 管理批量买入节奏、轮询持仓、执行五档 FOK 卖出与 claim |
| `Volatility_arbitrage_main_rest.py` / `Volatility_arbitrage_main_ws.py` | 连通性工具 | 建立 REST 客户端、校验签名配置、测试 WS 订阅 |
| `auto_sell_pnl.py` | 盈亏辅助 | 提供历史盈亏计算与卖出参考实现 |
| `poly_filter.py` | 老版筛选脚本 | 供回溯与参数比对使用 |
| `Volatility_config.py` | 配置中心 | 管理筛选阈值、节奏、日志工具 |

## 快速开始
1. **获取代码并创建虚拟环境**
   ```bash
   git clone <repo-url>
   cd POLYMARKET3
   python -m venv .venv
   source .venv/bin/activate  # Windows 使用 .venv\Scripts\activate
   ```
2. **安装依赖**
   ```bash
   pip install py-clob-client websocket-client requests
   ```
3. **配置环境变量（REST 私钥）**
   ```bash
   export POLY_KEY="<私钥十六进制，可含0x>"
   export POLY_FUNDER="<充值地址 Proxy Wallet>"
   export POLY_HOST="https://clob.polymarket.com"  # 可选
   export POLY_CHAIN_ID="137"                      # 可选
   export POLY_SIGNATURE="2"                       # 可选
   ```
4. **运行入口脚本**
   ```bash
   python Volatility_arbitrage_run.py
   ```
   程序会先验证 REST / WS 连通性，再交互式询问盈利阈值、余额阈值、买入节奏与监控间隔，然后循环执行买入与盈利监控。

## 运行流程速览
1. 启动并打印配置快照。
2. 验证 REST/WS 连接与账户可下单能力。
3. 读取或输入盈利阈值、买入节奏、资金阈值等参数。
4. 调用筛选器获取候选市场并按节奏批量买入。
5. 每轮按设定间隔检查持仓盈亏，触发卖出或 claim。
6. 余额恢复后重新进入筛选-买入循环，直至人工终止。

## 注意事项
- 套件主要面向策略原型验证，缺乏完备测试，请先在沙盒或极小额度环境验证稳定性。
- 关注 `Volatility_config.py` 中的筛选参数及日志设置，调整后需确保与入口脚本交互提示一致。
- Polymarket API 可能出现限流或数据延迟，建议结合外部监控与重试机制以提升稳健性。

祝你顺利完成扫尾与回款！
