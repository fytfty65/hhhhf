# 周期评估任务部署

周期任务只负责读取事件、生成离线评估报告，并将经过字段白名单过滤的聚合指标写回 Gateway。它不会晋级模型，也不会把原始事件、路线或凭据发送到 Gateway。

## 环境变量

在部署机器的受保护环境中设置，不要提交到 Git：

```text
AI_SERVICE_INTERNAL_TOKEN=由部署系统注入
MODEL_EVALUATION_GATEWAY_URL=http://gateway:8080/internal/v1/planning/models
MODEL_EVALUATION_LOCK_FILE=/var/lib/omniroute/model-evaluation.lock
MODEL_EVALUATION_AUDIT_FILE=/var/log/omniroute/model-evaluation.jsonl
MODEL_EVALUATION_MODEL_ID=1
MODEL_EVALUATION_MODEL_VERSION=planner-v4
```

`AI_SERVICE_INTERNAL_TOKEN` 必须与 Gateway 的 `AI_SERVICE_INTERNAL_TOKEN` 一致。`MODEL_EVALUATION_MODEL_ID` 和 `MODEL_EVALUATION_MODEL_VERSION` 用于选择待评估的已注册版本。文档、日志和告警中都不能打印 Token。

## Linux systemd timer

复制 `omniroute-model-evaluation.service` 和 `omniroute-model-evaluation.timer` 到 `/etc/systemd/system/`，按实际路径修改 service 中的 `WorkingDirectory`、数据库路径和模型 ID，然后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now omniroute-model-evaluation.timer
systemctl list-timers omniroute-model-evaluation.timer
```

默认每 30 分钟执行一次。任务失败不会晋级模型；下一次 timer 会重新尝试。锁文件避免同一节点重入。

## Windows Task Scheduler

使用 `run-model-evaluation.ps1` 创建任务。脚本通过项目虚拟环境执行 Python 模块，并把结果写入脱敏 JSONL 审计文件。首次部署时在系统环境变量或受保护的任务环境中设置 Token，不要把 Token 写入脚本参数。

建议每 30 分钟运行，任务设置中启用：

- 如果任务失败，每 10 分钟重试，最多 3 次；
- 任务运行超过 10 分钟自动停止；
- 使用专用服务账号运行；
- 工作目录固定为 `ai-service`。

## Docker / 容器

容器编排器应使用独立的 cron sidecar 或平台定时任务调用：

```text
python -m tools.run_model_evaluation \
  --db /data/gateway/omniroute.db \
  --model-id 1 \
  --model-version planner-v4
```

Token 通过 secret 注入，数据库以只读卷挂载。审计 JSONL 使用单独可写卷，并设置轮转策略。

## 验收标准

1. 无 Token 或 Gateway 不可达时任务返回阻断/错误，不能伪造 ready。
2. 数据不足时返回 `insufficient_evidence`，且 `promotion_ready=false`。
3. 重复启动两个任务时只有一个持有锁。
4. 审计记录只包含状态、版本、样本数量和回写结果，不包含 Token、原始事件或用户数据。

策略对照报告还会按用户群体、目的地、季节和预算分层输出覆盖率。分层样本不足时显示
`insufficient_data`，不会把其他分层的 reward 借用过来，也不会因此宣称策略已在该群体上变优。

## Staging smoke check

在接入真实 Gateway 之前，可用只读数据库执行脱敏契约烟测：

```bash
python -m tools.run_staging_smoke \
  --db /data/gateway/omniroute.db \
  --model-version "$MODEL_EVALUATION_MODEL_VERSION"
```

该命令不会写 SQLite、不会调用供应商或模型端点，也不会使用/打印 Token；它只运行离线评估，校验回写字段白名单，并输出样本数、验证集数量、分层公平状态等聚合结果。真实 Gateway 回写仍由
`tools.run_model_evaluation` 在受保护环境中执行。

如果要在 staging 明确演练一次 Gateway 回写，可显式加 `--callback`。Token 只从受保护的
`AI_SERVICE_INTERNAL_TOKEN` 环境变量读取，不接受命令行参数，也不会输出：

```bash
STAGING_SMOKE_CALLBACK_URL=http://gateway:8080/internal/v1/planning/models/1/evaluation \
AI_SERVICE_INTERNAL_TOKEN="$AI_SERVICE_INTERNAL_TOKEN" \
python -m tools.run_staging_smoke --db /data/gateway/omniroute.db \
  --model-version "$MODEL_EVALUATION_MODEL_VERSION" --callback
```

不加 `--callback` 时不会发生任何网络写入；回写演练只返回 HTTP 状态码和 accepted 布尔值。
回写被判定为成功的条件是 Gateway 返回 2xx 且标准契约中的 `data.accepted=true`；空响应、代理 HTML 或其他 2xx 内容都会被标记为失败。

Windows 可使用同目录的 `run-staging-smoke.ps1`，数据库仍以只读方式打开：

```powershell
.\run-staging-smoke.ps1 -Database 'D:\data\omniroute.db' -ModelVersion $env:MODEL_EVALUATION_MODEL_VERSION
```
