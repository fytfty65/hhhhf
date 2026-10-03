# 供应商质量发布门禁 v1

供应商质量事件只保存聚合计数，不保存供应商原始响应、查询 URL、用户标识、行程标识或凭据。离线评估将这些事件单独聚合为 `provider_quality`，不会把它们混入规划 reward 样本。

## 状态

- `not_required`：数据集没有供应商质量事件。旧数据集可以继续做决策评估，但不会声称供应商健康。
- `insufficient_data`：存在质量事件，但某个领域/供应商少于 5 次观测或没有候选。不能判定优劣，模型发布门禁保持关闭。
- `blocked`：证据充分但质量超阈值，模型发布门禁保持关闭。
- `available`：所有已观测领域/供应商均满足阈值，可以进入人工复核和灰度流程。

## 默认阈值

| 指标 | 门限 |
| --- | --- |
| 最少观测次数 | 5 |
| 过期比例 | ≤ 20% |
| 多来源冲突比例 | ≤ 20% |
| 枚举异常比例 | ≤ 5% |
| 决策可用比例 | ≥ 70% |

质量门禁是输入边界安全门，不代替硬约束、离线 reward、公平性、数据漂移和反事实评估。只有所有门禁都通过，灰度版本才会被运行时分配流量；灰度期间仍由运行时样本和自动回滚监控保护。

## 数据流

1. Gateway 在供应商候选归一化后记录 `provider_quality` 聚合事件。
2. `build_decision_dataset` 以 `kind:provider` 聚合质量计数，拒绝复制原始供应商字段。
3. `evaluate_decision_dataset` 输出 `provider_quality_gate`，证据不足或超阈值会阻断 `promotion_ready`。
4. `run_model_evaluation` 只向 Gateway 回写状态和计数；Gateway 模型注册表将其持久化并接入运行时门禁。

调度器本地 JSONL 审计也会记录同一批脱敏聚合字段：
`provider_quality_status`、`provider_quality_providers`、
`provider_quality_ready_providers`、`provider_quality_insufficient_providers`、
`provider_quality_blocked_providers`。Gateway 的评估审计接口只允许这些状态和计数
通过，便于把“本次任务阻断”与供应商质量原因关联；原始供应商响应、查询 URL、用户/行程
标识和凭据始终不会进入审计接口。

规划质量接口的 `provider_quality_gate.alerts` 是运营告警摘要，格式为：

```json
{
  "code": "provider_quality_stale_data",
  "severity": "critical",
  "affected_providers": 1
}
```

当前稳定告警码包括：`provider_quality_blocked`、
`provider_quality_insufficient_data`、`provider_quality_stale_data`、
`provider_quality_source_conflict`、`provider_quality_enum_invalid` 和
`provider_quality_low_eligibility`。前端只展示告警码、级别和受影响数量，不展示供应商
原始身份或响应内容。

`provider_quality_gate.alert_digest` 是当前告警集合的短指纹，供后续 Web Push/邮件 worker
配合冷却窗口去重；它不是供应商标识，也不能反推出原始响应。

Gateway 会为每个用户/行程范围保存脱敏的告警状态（仅保存范围哈希、指纹、状态和时间戳）。
默认冷却窗口为 30 分钟，可通过部署环境变量
`PROVIDER_QUALITY_ALERT_COOLDOWN_SECONDS` 调整，范围限制为 60 秒至 7 天。查询接口
`GET /api/v1/planning/providers/quality/alerts` 只返回指纹和投递计数，不返回用户或行程标识；
它仍然不是“已发送”证明，真正的 Web Push/邮件发送必须在用户明确授权后由通知 worker 执行。

当告警首次出现或冷却窗口结束时，Gateway 会幂等写入一条站内 `RiskNotification`：
`type=provider_quality_gate`、`source=omniroute-quality-gate`，状态为 `unread`。这条记录
只使用固定的安全文案和严重级别，不携带供应商名称、原始响应、请求 URL 或密钥；冷却期内不会
重复生成。现有风险收件箱可直接读取这类通知，外部 Web Push/邮件仍需用户授权和独立 worker。

同时会写入一条 `ProviderQualityAlertDelivery` 投递账本，初始状态为 `pending`，渠道为
`in_app`，并保留尝试次数、最近错误、下次尝试时间和发送时间字段。诊断接口
`GET /api/v1/planning/providers/quality/alerts/delivery` 只返回这些生命周期字段；当前不会
自动调用 Web Push/邮件。后续 worker 应先领取 `pending/retrying` 记录，再按授权渠道更新为
`sent` 或 `failed`，并使用退避策略设置 `next_attempt_at`。

投递状态机约定：worker 领取后进入 `sending` 并获得 5 分钟租约；租约过期会自动回收为
`retrying`。失败时按 30 秒、60 秒、120 秒递增退避，默认最多尝试 5 次，超过上限进入
`failed`。可通过 `PROVIDER_QUALITY_DELIVERY_MAX_ATTEMPTS` 调整上限（1 至 20 次）。
当前领取、成功和失败函数只更新账本，不执行外部网络发送。

如果 FCM 只返回明确失效的设备 token（没有成功设备，但所有结果都已分类为失效），该投递会
进入终态 `sent` 并停止重试；失效 token 已被清理，继续重试不会产生进展。只有存在未分类的
发送失败时才进入退避重试。

Gateway 内置的 Web Push worker 默认关闭。只有显式设置
`PROVIDER_QUALITY_WEB_PUSH_WORKER_ENABLED=true`，并且部署环境提供有效的 FCM 服务账号配置时
才会启动；可用 `PROVIDER_QUALITY_WEB_PUSH_INTERVAL_SECONDS`（30 秒至 24 小时）和
`PROVIDER_QUALITY_WEB_PUSH_BATCH_SIZE`（1 至 100）调整调度。配置错误只会记录“worker 未启动”，
不会打印凭据，也不会影响规划请求。

worker 的聚合运行指标会进入 `/metrics/prometheus`，仅包含领取、发送、重试、失败、失效 token、
无 token、dry-run 和清理计数，不包含用户、行程、通知正文、设备 token 或 FCM 响应。指标名为
`omniroute_provider_quality_push_total` 与
`omniroute_provider_quality_delivery_cleanup_total`，可用于告警，例如连续失败增长或清理任务
长期没有运行。

投递账本的 `sent`/`failed` 终态记录默认保留 30 天，之后由 worker 在调度周期中清理；可通过
`PROVIDER_QUALITY_DELIVERY_RETENTION_DAYS`（1 至 3650 天）和
`PROVIDER_QUALITY_DELIVERY_CLEANUP_INTERVAL_SECONDS`（60 秒至 7 天）调整。清理不会删除
告警状态或站内通知。staging/test 可设置
`PROVIDER_QUALITY_WEB_PUSH_DRY_RUN=true`，worker 只统计待发送条数，不领取、不发送、不修改
投递状态，便于联调调度和指标而不触碰真实推送。

`/readyz` 会显示 `provider_quality_web_push` 检查项。worker 未开启时为 `disabled`；staging
dry-run 为 `dry_run`；开启真实 worker 但 FCM 配置缺失时为 `warning`，生产环境会让 readiness
返回不可用，避免把无法投递的部署误判为健康。
5. 前端专业层显示状态、通过/不足/阻断数量和有限的异常原因。

样本不足不会被补零，也不会显示“供应商已健康”或“模型已学会”。真实价格、库存、余票、天气和客流仍必须来自带来源与时间戳的供应商字段；估算值只能作为估算展示。
