# 交易状态异常的可能原因说明

根据 `Volatility_arbitrage_run.py` 的买入流程，出现 `[BUY][SKIP] 当前状态非 FLAT 或仍有持仓/待确认订单，丢弃买入信号。` 的前提是：

1. 策略状态机 `strategy.status()` 返回的 `state` 不是 `FLAT`，或 `awaiting` 不为空（例如上一次 BUY/SELL 还在等待确认）。代码在强制刷新状态后再次检查仍满足条件时就会打印该提示并拒绝买入。【F:Volatility_arbitrage_run.py†L2538-L2555】
2. 进入该检查前，脚本会先通过 `_maybe_refresh_position_size` 把链上仓位同步到本地。若查询到的总仓位 `total_pos` 与本地记录不一致，会调用 `strategy.on_sell_filled` 或 `strategy.on_buy_filled` 强制修正状态；若远端仓位被视为大于 `dust_floor`（默认取 `max(API_MIN_ORDER_SIZE, 1e-4)`，而 `API_MIN_ORDER_SIZE` 在脚本顶部写死为 `5.0`），则策略会被认定仍有仓位/待卖出，从而保持 `awaiting=SELL` 或 `state=LONG`。【F:Volatility_arbitrage_run.py†L2161-L2190】【F:Volatility_arbitrage_run.py†L2323-L2368】

结合你提供的日志，虽然输出的 `state=FLAT awaiting=None`，但在进入 BUY 分支时很可能因为远端返回的 `remaining` 或 `total_pos` 大于等于 5（`API_MIN_ORDER_SIZE`）而被判定“尚有仓位”，`_awaiting_blocking` 返回真，导致买入被跳过。即使网页端显示已卖出，若接口在短时间内返回了非零剩余（比如小于 5 的残余或尚未结算的数量），脚本会把它当作待清仓仓位，从而抑制新的 BUY 信号。

若要验证，可在运行时关注 `_maybe_refresh_position_size` 输出的 `[WATCHDOG][POSITION] ... size=...` 以及卖出回调中的 `sell_remaining` 值，确认是否存在被 `API_MIN_ORDER_SIZE` 阈值拦住的小额残余。

## 对“残余仓位导致买入卡死”逻辑的现状评估

- 买入前置检查里会取本地 `position_size` 与策略 `status()` 的 `position_size`，若大于等于 `API_MIN_ORDER_SIZE`（dust_floor）则打印 `[BUY][BLOCK]` 并先下卖单；若小于 dust_floor 则走 `[BUY][DUST]` 分支，直接调用 `strategy.on_sell_filled(..., remaining=0)` 把状态改回 FLAT 后继续尝试买入。【F:Volatility_arbitrage_run.py†L2502-L2536】
- 但如果策略层的 `_awaiting` 被卡在 SELL（例如没有收到 on_sell_filled 回调、或未走到 `[BUY][DUST]`），`_awaiting_blocking` 会一直为 True。买入前的两次状态同步都只检查“state 是否 FLAT、awaiting 是否为空”，没有额外逻辑清理等待状态，所以会打印 `[BUY][SKIP]` 后直接 `on_reject`，停留在等待买入的循环中。【F:Volatility_arbitrage_run.py†L2537-L2556】【F:Volatility_arbitrage_strategy.py†L393-L417】
- `_maybe_refresh_position_size` 仅同步仓位数值，当 API 返回仓位为 0 但策略 `_awaiting` 仍为 SELL 时，它不会触发尘埃分支，也不会自动视为 sell 完成；因此等待状态得不到释放，新的买入信号会被持续跳过。【F:Volatility_arbitrage_run.py†L2146-L2166】【F:Volatility_arbitrage_strategy.py†L373-L417】

综上，脚本没有对“等待状态未解除”的角落进行兜底处理，存在用户描述的“什么都不做，继续等待买入，形成死循环”的逻辑漏洞。

## 可能的解决思路（仅思路，不改代码）

1. **在买入前置检查中兜底清理等待状态**：若 `_maybe_refresh_position_size` 返回 0 且 `_awaiting` 仍为 SELL，可视为“卖单已完成但回调缺失”，直接调用 `on_sell_filled(..., remaining=0)` 或显式清空 `_awaiting`，再进入 BUY 流程，防止卡在 `[BUY][SKIP]`。【F:Volatility_arbitrage_run.py†L2146-L2166】【F:Volatility_arbitrage_run.py†L2537-L2556】【F:Volatility_arbitrage_strategy.py†L373-L417】
2. **尘埃仓位自动忽略**：当链上/本地仓位小于 `API_MIN_ORDER_SIZE` 时，即便 `_awaiting` 仍为 SELL，也应走尘埃处理路径，调用 `on_sell_filled` 将状态重置为 FLAT，而不是仅依赖仓位大于阈值时的 `[BUY][BLOCK]` 流程。【F:Volatility_arbitrage_run.py†L2323-L2368】【F:Volatility_arbitrage_run.py†L2502-L2536】
3. **卖单回调的健壮性增强**：在下单与回调之间增加超时/确认机制，若超过一定时间仍未收到卖单填充事件（但仓位已归零），主动触发一次 `on_sell_filled`，或重新查询订单状态决定是补发卖单还是清除等待标记。【F:Volatility_arbitrage_strategy.py†L373-L417】
4. **监控日志与告警**：对重复出现的 `[BUY][SKIP]` 加入计数阈值与告警输出，提示操作员当前状态被等待标记阻塞，以便人工干预或触发自恢复逻辑。

这些思路的核心是：让“仓位为 0 时的等待状态”能够被自动化清理或忽略尘埃，从而避免买入端的死循环卡死。

## 修改后逻辑的再核验

- `Volatility_arbitrage_run.py` 已在 `_maybe_refresh_position_size` 中新增兜底：当仍处于 SELL 待确认但链上仓位为 0 或小于 `API_MIN_ORDER_SIZE` 时，强制调用 `strategy.on_sell_filled(..., remaining=0)`，同步为空仓并解除等待状态，不再卡在 `[BUY][SKIP]` 循环。【F:Volatility_arbitrage_run.py†L2168-L2200】
- 该函数被以下路径调用：
  - 主循环的定时 watch-dog，每 60 秒会主动刷新一次仓位，即便没有行情触发也能清理等待状态。【F:Volatility_arbitrage_run.py†L2170-L2182】【F:Volatility_arbitrage_run.py†L2431-L2434】
  - 买入前置检查中，当检测到状态非 FLAT 或有等待标记时，会强制刷新仓位并再次检查，确保在产生 BUY 信号时也能及时释放 SELL 卡死。【F:Volatility_arbitrage_run.py†L2537-L2555】
- 策略层的 `on_sell_filled` 本身支持“尘埃仓位视为清空”，因此上述兜底调用会将 `_awaiting` 和 `_state` 一并复位，买入检查中的 `_awaiting_blocking` 将返回 False，循环得以继续。【F:Volatility_arbitrage_strategy.py†L373-L417】

综合上述流程，等待卖出状态而链上仓位已清空的场景会被自动识别并清理，原先的死循环出口已补齐。
