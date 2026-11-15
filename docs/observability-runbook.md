# OmniRoute 可观测性运行手册

本阶段把供应商质量 Web Push 的运行状态接入 Prometheus。所有指标都是进程级聚合计数，不能反推出用户、行程、通知正文、设备 token 或供应商响应。

## 接入

Prometheus 抓取：

```yaml
scrape_configs:
  - job_name: omniroute-gateway
    metrics_path: /metrics/prometheus
    static_configs:
      - targets: ["gateway:8080"]
```

生产环境必须配置 `METRICS_TOKEN`，抓取器使用 `X-Metrics-Token` 或 `Authorization: Bearer` 访问。指标端点不会通过公共网络匿名开放。

将 `deploy/prometheus/omniroute-alerts.yml` 加入 Prometheus `rule_files`。规则只在 worker 显式开启时触发，staging dry-run 不会触发真实推送故障告警。

## 关键指标

| 指标 | 含义 |
| --- | --- |
| `omniroute_provider_quality_push_total{outcome="claimed"}` | 领取的 Web Push 投递数量 |
| `...{outcome="sent"}` | 成功发送数量 |
| `...{outcome="retrying"}` | 进入重试数量 |
| `...{outcome="failed"}` | 达到最大尝试次数后失败数量 |
| `...{outcome="invalid_token"}` | FCM 判定失效并清理的 token 数量 |
| `...{outcome="no_token"}` | 用户没有授权设备时的投递尝试数量 |
| `omniroute_provider_quality_delivery_cleanup_total{outcome="deleted"}` | 清理的终态账本行数 |
| `omniroute_provider_quality_delivery_cleanup_last_success_timestamp_seconds` | 最近一次成功清理的 Unix 时间戳 |
| `omniroute_provider_quality_push_last_run_timestamp_seconds` | 最近一次 worker 调度运行的 Unix 时间戳 |

## 告警处置

1. `OmniRouteProviderPushWorkerErrors`：先查看 `/readyz` 的 `provider_quality_web_push`，再查看 gateway 日志；如果 FCM 配置缺失，生产 readiness 会变为不可用。
2. `OmniRouteProviderPushFailures`：检查 FCM 返回状态、授权 token 生命周期和 `PROVIDER_QUALITY_DELIVERY_MAX_ATTEMPTS`，不要手工把 `failed` 改回 `pending`。
3. `OmniRouteProviderPushDeliveryStalled`：检查数据库连接、租约回收和进程是否持续运行；投递状态机允许过期 `sending` 自动回到 `retrying`。
4. `OmniRouteProviderDeliveryCleanupStalled`：检查 worker 调度和数据库写入。该告警使用最近一次成功清理时间戳，而不是 Prometheus 抓取时间；清理只删除超过保留期的 `sent/failed` 行，不会删除告警状态或站内通知。
5. `OmniRouteProviderPushInvalidTokenSpike`：检查前端是否重复注册 token、用户是否批量更换浏览器；失效 token 会被服务端清除，不应无限重试。

FCM 响应分类遵循保守策略：HTTP 404 或明确的 `UNREGISTERED`/`INVALID_ARGUMENT` 才会把设备 token 判为失效并清理；普通 HTTP 400 不会自动删除 token，因为它可能是消息 payload 配置错误。这样可以避免把服务端配置问题错误地归因于用户授权状态。

## Staging 验证

staging 可以使用：

```text
PROVIDER_QUALITY_WEB_PUSH_WORKER_ENABLED=true
PROVIDER_QUALITY_WEB_PUSH_DRY_RUN=true
PROVIDER_QUALITY_WEB_PUSH_INTERVAL_SECONDS=30
```

dry-run 只统计待发送记录，不领取、不发送、不改变账本状态。验证完成后必须关闭 dry-run，并在具备真实 FCM 凭据及授权设备的隔离 staging 项目中做一次真实发送与失败重试测试；本地开发不应指向生产 Firebase 项目。

Gateway 还提供单次调度函数 `RunProviderQualityWebPushOnce` 供受控 smoke 测试调用。它不会启动常驻 worker；在 dry-run 模式下只返回待投递数量，因此测试可以立即验证调度契约，不需要等待定时器，也不会触碰真实推送网络。

也可以直接运行 Gateway smoke 命令：

```powershell
cd gateway
$env:APP_ENV = "staging"
go run ./cmd/provider-quality-smoke -db "D:\data\omniroute-staging.db" -limit 20
```

命令只接受 dry-run（默认开启），必须处于 `staging` 或 `test` 环境；`-dry-run=false` 会直接阻断。输出仅包含状态、批次上限和待投递数量，不输出数据库内容、用户标识、通知正文、设备 token 或任何凭据。

真实 staging 推送前先执行静态配置预检：

```powershell
cd gateway
$env:APP_ENV = "staging"
go run ./cmd/provider-quality-preflight -env staging
```

预检只检查环境白名单、服务账号文件是否存在且可解析、项目 ID 是否可用；不会请求 OAuth、不会调用 FCM，也不会输出路径、项目 ID、私钥或解析错误原文。只有输出 `"status": "ready"` 才允许进入隔离 staging 的真实发送步骤。

## 外部探活与资源告警（阿里云云监控）

`/admin` 看板是"人主动去看"，进程崩溃、磁盘写满时没人会去看。用阿里云云监控做**主动通知**，两条探活 + 三条资源阈值即可覆盖绝大多数"整站打不开"的故障。

### 探活（站点监控 / HTTP）

| 监控地址 | 期望 | 覆盖的故障 |
| --- | --- | --- |
| `http://118.178.237.166/` | 200，响应 < 3s | Nginx、前端进程、证书/端口链路；前端挂了这里最先红 |
| `http://118.178.237.166/readyz` | 200 且 `"status":"ready"` | 网关依赖：SQLite ping、Redis ping（生产必需）；返回 503 表示依赖不可用 |

建议参数：探测频率 **1 分钟**、超时 **5 秒**、连续 **3 次**失败才告警（避免单次抖动）、探测点选多个运营商。报警规则同时匹配"状态码非 200"和"响应时间 > 3000ms"。

`/healthz` 只返回网关进程存活（不查数据库、不查 Redis），**不要作为唯一探针**：数据库挂掉时它仍然是 200。它适合作为第二个低成本存活检查。

当前线上实测基线：`/` 约 200ms 返回 200，`/readyz` 返回 `{"checks":{"database":"ok","redis":"ok"},"status":"ready"}`。

### 资源阈值

| 指标 | 阈值 | 说明 |
| --- | --- | --- |
| CPU 使用率 | > 85% 持续 5 分钟 | 构建/异常请求打满的早期信号 |
| 内存使用率 | > 90% 持续 5 分钟 | 机器 1.7G，接近上限时先看 `free -m` 与 swap |
| 磁盘使用率（`/`） | > 85% | 数据库 + 备份 + `.next` 都在这块 40G 盘上 |

内存与磁盘**使用率**需要 ECS 内安装云监控插件（`cloudmonitor`）。本文档记录时该实例**未安装**，所以云监控只有 CPU、网络、磁盘 IO 等基础指标 —— 装插件之前，"内存 90%""磁盘 85%"这两条规则不会触发。

### 通知渠道

- 邮件/短信：云监控「报警联系组」直接加联系人邮箱、手机号；
- 微信：个人微信不支持，使用**企业微信**群机器人（云监控报警规则里可选"企业微信"或 Webhook），把机器人 Webhook 填进去即可；
- 收到 `/readyz` 告警的处置顺序：先看返回体里的 `checks`（哪一项不是 ok），再看 `systemctl status omniroute-gateway` 与 `journalctl -u omniroute-gateway -n 50`；`/` 告警则先看 `systemctl status omniroute-front` 与 Nginx 错误日志。

## 当前边界

这些规则不能证明真实供应商价格、余票、天气或客流数据正确，也不能替代供应商联调。没有商业资质时，平台仍只做可信数据聚合、规划建议和官方渠道导流，不在平台内完成支付或出票。
