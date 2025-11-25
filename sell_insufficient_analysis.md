# 卖出阶段反复缩减数量的原因分析

## 现象
日志中多次出现 `[MAKER][SELL] 可用仓位不足，调整卖出数量后重试`，数量每次下调 0.01，说明下单接口持续返回仓位不足。

## 触发路径
- `maker_execution.py` 的 `maker_sell_follow_ask_with_floor_wait` 在进入循环时，将入参 `position_size` 作为 `goal_size`，并用它推导剩余数量 `remaining`。【F:maker_execution.py†L720-L778】
- 若未提供 `position_fetcher` 或刷新间隔未到，`remaining` 不会根据实时仓位更新，即使仓位已被其它途径卖出也保持原值。只有下单失败才触发缩量逻辑。刷新逻辑只在定时器命中时调用 `position_fetcher`，否则不会修正 `goal_size`。【F:maker_execution.py†L823-L908】
- 调用 `adapter.create_order` 如果返回包含 `insufficient/balance/position` 的错误，代码会将目标数量按 `shrink_tick`（初始 0.01，重试超过 100 次后升至 0.1）递减，并更新 `goal_size` 与 `remaining` 后立刻重试。【F:maker_execution.py†L985-L1027】

## 根因
当真实仓位已被外部成交或小于最小挂单量，而当前循环未刷新实时仓位时，内部仍认为有 `remaining` 可卖。下单被交易所拒绝为“仓位不足”后，逻辑只做机械式缩量重试，没有重新读取实际仓位，也没有在多次失败后停下。因此会出现一串 0.01 递减的重试日志。

## 解决思路
- **强制刷新仓位**：在收到“仓位不足”异常时调用 `position_fetcher`（如查询钱包/合约持仓），用返回值直接重设 `goal_size`/`remaining`，如果结果低于最小挂单量则终止卖出流程。
- **提高刷新频率或必填**：将 `position_fetcher` 设为必选并缩短 `position_refresh_interval`，确保卖单与真实仓位同步；或者在卖出前再次校验可用仓位。
- **失败熔断**：为连续多次 `insufficient` 计数设置上限，超过后退出并提示仓位不足，而不是无限缩量重试。
