# OmniRoute

基于多智能体与大语言模型的中文旅行路线规划平台：从「行前规划 → 行程执行 → 行中风控 → 行后复盘」的完整闭环。

本文件是新接手者的入口。项目此前的知识散落在 `docs/`、`ai-service/docs/`、`SPLIT_PLAN.md` 和一份很长的
`EVALUATION_REPORT.md` 里，但缺一份「怎么跑起来、每层负责什么、环境变量要填什么」的根说明。

---

## 1. 架构

三个进程 + 一个可选依赖。**网关与 AI 服务必须在各自目录下启动** —— 两者都按相对路径读 `.env` 与数据文件。

| 组件 | 技术栈 | 默认地址 | 职责 |
|---|---|---|---|
| `front/` | Next.js 16 / React 19 / TypeScript | `http://<host>:3000` | 交互界面、地图、实时协作、CSP 与无障碍 |
| `gateway/` | Go 1.26 / Gin / GORM + SQLite | `http://<host>:8080` | 鉴权与限流、业务数据、WebSocket、鉴权编排、指标 |
| `ai-service/` | Python / FastAPI | `http://127.0.0.1:8000` | 规划算法、LLM 推演、取价与风控、评测 |
| Redis | — | `127.0.0.1:6379` | 会话共享、事件流、语义缓存、分布式限流 |

调用方向是单向的：浏览器 → 网关 → AI 服务 → 外部数据源。**AI 服务默认只绑回环**，且默认要求
`X-Omni-Internal-Token`（`AI_SERVICE_INTERNAL_TOKEN`），因此它不对外提供匿名服务。

```
浏览器 ──HTTP/WS──> gateway :8080 ──HTTP──> ai-service :8000 ──> LLM / 搜索 / 高德 / 票务 / 风控源
                        │
                        └── SQLite (gateway/data/omniroute.db) + Redis
```

---

## 2. 快速开始（本机开发）

前置：Go 1.26+、Node 20+、Python 3.14（以 `ai-service/.python-version` 为准）、Redis（可选但不启动会降级）。

### AI 服务

Python 版本固定在 `ai-service/.python-version`（当前 `3.14`），CI 与本机都读这一处，
不再各写一个版本号。

Windows 一键初始化（推荐）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/init-ai-service.ps1
```

脚本会创建 `ai-service/.venv`、按 `requirements-dev.txt` 安装锁定版本，并跑一次
启动前依赖检查。重复执行是安全的：已有 `.venv` 会复用。部署机只需要运行时依赖时加
`-RuntimeOnly`。

手动等价步骤：

```powershell
cd ai-service
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env        # 至少填 LLM_API_KEY
.\.venv\Scripts\python.exe main.py
```

> **必须用项目内的 `.venv`，不要用系统全局 Python。** `requirements.txt` 是锁定版本；
> 全局环境里已有的不同版本会让「本地测试通过」不再说明任何问题。`main.py` 启动时会先做
> 依赖检查（解释器版本 + 每个锁定包是否存在且能导入），不满足会直接打印修复命令并退出，
> 而不是等运行到一半才抛 `ModuleNotFoundError`。

不配 `LLM_API_KEY` 时服务仍能启动，但真实推演会失败并如实降级（不会编造路线）。默认模型与端点在
`core/llm_config.py` —— 那是模型名/端点/温度的**唯一事实来源**，不要在别处再写一套兜底。

### 网关

```bash
cd gateway
cp .env.example .env
go build -o gateway.exe ./cmd/server      # Windows
./gateway.exe
```

本机开发不必启动 Redis（会退回进程内会话存储）；`APP_ENV=production` 时 Redis 与
`METRICS_TOKEN` 是**强制**的，缺了会直接启动失败（这是故意的，避免指标端点裸露）。

### 前端

```bash
cd front
npm ci
cp .env.example .env.local
npm run build && npm run start     # 生产模式；多人测试不要用 next dev
```

默认无需配任何环境变量：前端会自行推导「当前页面主机 + 8080」作为 API 基址。`NEXT_PUBLIC_*`
是**构建期**注入的，改完必须重新 `npm run build`。

---

## 3. 环境变量

各服务的完整清单见 `gateway/.env.example`、`ai-service/.env.example`、`front/.env.example`。
下面只列**容易踩坑**的几项。

### 网关

| 变量 | 说明 |
|---|---|
| `APP_ENV` | `production` 会打开多项硬校验（Redis、METRICS_TOKEN 必填） |
| `ALLOWED_ORIGINS` | 精确来源白名单，逗号分隔。**绝不要用 `*`**。多人测试必须包含朋友的完整来源 |
| `TRUSTED_PROXIES` | **安全项**：允许哪些对端通过 `X-Forwarded-For` 声明客户端 IP。留空 = 谁都不信（直连场景正确）；前面挂了反代/负载均衡才填网段。填错会直接启动失败 |
| `METRICS_TOKEN` | `/metrics` 与 `/metrics/prometheus` 的访问令牌；生产必填 |
| `REDIS_URL` | 生产必填（会话共享 + 分布式限流） |
| `EVENT_RETENTION_ENABLED` / `EVENT_RETENTION_DAYS` | 四张按「使用量」增长的表（`messages`、`planning_events`、`recommendation_events`、`feedback_logs`）的保留窗口。**默认关闭**：升级时静默删除用户聊天/反馈记录属于数据丢失，必须由运维显式开启。窗口会被夹到 `[30, 3650]`，超出范围回退默认 365 天 |

### AI 服务

| 变量 | 说明 |
|---|---|
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_NAME` | 默认走 DashScope 兼容口 + `qwen-turbo`；换 DeepSeek/Ollama 等只需改这三项 |
| `AI_SERVICE_INTERNAL_TOKEN` | 网关调用本服务时带的共享密钥，须与网关侧一致 |
| `AI_SERVICE_ALLOW_ANONYMOUS` | 显式关闭内部鉴权。**只在纯本机运行且不可外部访问时使用** |
| `AMAP_API_KEY`、`SENIVERSE_API_KEY` | 高德 POI / 天气 |
| `WEB_PRICE_*` | 联网取价的开关与预算（见下节「诚实未知」） |

### 前端

`NEXT_PUBLIC_API_BASE`（默认按访问主机推导）、`NEXT_PUBLIC_WS_BASE`、`NEXT_PUBLIC_FIREBASE_*`（Web Push，可选）。

---

## 4. 测试与质量门禁

```bash
# Go
cd gateway && go build ./... && go test ./... && go vet ./...

# Python：一条命令跑完 预检 + unittest + ruff + mypy + pip-audit
cd ai-service
.venv\Scripts\python.exe tools\check.py          # Windows
# .venv/bin/python tools/check.py                # macOS/Linux

# 前端
cd front && npm run test && npm run lint && npx tsc --noEmit && npm run build && npm run test:e2e
```

CI（`.github/workflows/ci.yml`）在 push/PR 上跑上面三条链路。注意两点：

- Python 侧 CI 不另写命令，直接执行 `python tools/check.py`，所以「本地绿」和「CI 绿」是同一套
  判定。ruff、mypy、pip-audit 都是**硬门禁**（基线已清零），新问题会让 CI 失败。
- Python 版本由 `ai-service/.python-version` 统一，CI 通过 `setup-python` 的
  `python-version-file` 读取。`tests/__init__.py` 仍然必需：没有它时
  `unittest discover -s tests` 在 3.11 上直接 `ImportError`。
- `tools/check.py` 里的 unittest 会有一组测试用子进程调用 `node` 渲染页面
  （`core/rendered_page.py`，渲染器在 `front/scripts/render-page.mjs`）。
  本地跑检查前请确认 `node` 已在 `PATH` 中；否则这 4 条会以
  `FileNotFoundError: node` 失败，属于环境缺失而非代码问题。

---

## 5. 这个项目的几条硬规矩

改动前请先理解这几条，它们不是风格偏好，而是产品可信度的基础：

1. **诚实未知**：拿不到的数据返回 `None`/`unknown`，绝不猜一个数字。注释里写作 `unknown ≠ 0`。
   价格分 `verified` / `estimated` / `unknown` 三级，且必须带出处 URL。
2. **单一事实来源**：模型参数只在 `core/llm_config.py`；CSP 白名单推导只在 `lib/cspTargets.js`
   （有契约测试钉住）。不要写第二套兜底默认值。
3. **失败要可见**：降级要登记（`core/degradation.py`）并下发到前端，而不是静默返回空。
4. **安全默认收敛**：AI 服务默认绑回环 + 默认要求内部令牌；网关默认不信任任何转发头；
   生产缺关键配置直接启动失败。
5. **抓来的网页内容是数据，不是指令**：任何进入提示词的第三方文本都必须当作不可信输入。

---

## 6. 已知遗留问题

- **`ai-service/api/agent.py` 仍是上帝模块**（抽取三批后约 8,100 行；`run_negotiate` 单函数仍有 5,700+ 行）。
  已拆出的三批：`core/negotiation_algos.py`（纯算法类）、`core/text_parsing.py`（零依赖工具函数）、
  `core/agent_contracts.py`（新增 `audit_route_agent_proposal`，与它校验的契约同处一地）；
  三者均由 `api.agent` 重新导出，调用方无需改动。
  **下一阶段的实测结论（重要）**：`run_negotiate` 里有 **38 个嵌套闭包**（含 5,347 行的 `event_stream`），
  而 **它们全部捕获了 `run_negotiate` 的局部变量** —— 也就是说"逐个搬函数"这条低成本路线已经用尽。
  再往下必须先把闭包共享的状态抽成一个显式的上下文对象（context object）并作为参数传入，
  属于一次设计级重构，需要独立分批进行，不能与其它改动混在一起。
- **端到端集成测试缺失**：`front/e2e/` 的用例全部 mock 后端；网关侧已补一条真实链路冒烟测试
  （`gateway/internal/api/realpath_smoke_test.go`：真实路由 + 真实 SQLite + 真实会话 + 真实 WebSocket），
  但「浏览器 → 网关 → AI 服务 → 外部源」整条接缝仍无自动化覆盖。
- **算法命名与实现不符**：`NashEquilibriumSolver` 名字叫纳什、实际只做帕累托非支配筛选；
  `TopologyFitnessCalculator` 是线性加权；`core/time_intelligence.py` 是关键词查表。
  **对用户的文案已修正**（不再出现「纳什博弈完成」），并由 `tests/test_output_honesty.py` 钉住；
  但类名仍是误导性的，改名是一次涉及导入面的独立改动。
- **SQLite 仍是单写模型**：`MaxOpenConns(1)` 保证了并发写入稳定，也决定了吞吐上限。短期已补
  busy timeout/重试、读路径分离、高频事件批量写入与复合索引；中期分工（Redis 实时事件与协同状态、
  PostgreSQL 持久化业务数据、SQLite 本地/单实例）见 `docs/storage-scaling.md`。
- 四张高频表（`messages`/`planning_events`/`recommendation_events`/`feedback_logs`）的保留窗口已实现，
  但**默认关闭**（`EVENT_RETENTION_ENABLED=false`）：不开启时它们仍会无界增长。
- **`ContextualLobby.tsx` 仍是巨型组件**：大厅入口（约 600 行）留在
  `ContextualLobby.tsx`，工作台已拆到 `front/app/components/UnifiedWorkspace.tsx`；
  用户会话 / 房间协同 / 规划结果 / 行程执行四类状态收敛在 `front/app/lib/stores/`。
  规划执行阶段的联动逻辑仍有进一步收敛空间。

---

## 7. 许可

本项目以 **MIT** 许可发布，见 `LICENSE`。

> 维护者请注意：该文件是评估时补上的（此前仓库没有任何授权声明，公开可见即等于「保留所有权利」）。
> 若你希望改用 Apache-2.0 或其它许可，直接替换 `LICENSE` 即可 —— 选 MIT 只是取最宽松、最常见的默认值。
