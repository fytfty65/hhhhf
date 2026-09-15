# OmniRoute 项目全面功能评估报告

> 评估对象：`D:\OminRoute_2\OminRoute\OminRoute`
> 评估日期：2026-05-19
> 评估方式：静态代码审计 + 三服务实测（真实启动 gateway / ai-service / front 并压测接口）+ 测试套件复跑 + Git 历史取证 + 构建产物计量
> 评估范围：核心功能完整性、用户体验流畅度、安全性、可扩展性
> 说明：本报告所有数字均为本次实测所得，标注「未验证」处表示未取得直接证据。

---

## 阶段一实施记录（2026-05-19 追加）

评估完成后已按用户确认的范围执行「阶段一：止血」。**范围限定为安全代码修改 + 停止跟踪敏感产物 + 删除死代码；明确未执行 git 历史重写，未轮换密钥**（需用户在高德/阿里云/心知天气控制台自行操作）。

### 已完成的 9 项修复 + 1 项额外发现

| # | 修改 | 文件 | 验证方式与结果 |
|---|---|---|---|
| E1 | `sessionRedis()` 的 `sync.Once` 永久缓存错误 → 改为互斥锁 + 5s 重试冷却；Redis 不可用时**回退进程内存会话**而非令认证 503 | `handlers/auth.go` | 实测：Redis 未运行时注册 **200 / 4453ms**、登录 **200 / 147ms**（修复前均为 503） |
| E2 | 登录/注册按客户端 IP 限流（登录 10/min、注册 5/min）+ 按账号 8 次失败锁定 15 分钟 + 未知用户走 dummy bcrypt 消除时序预言机 | `handlers/ratelimit.go`、`handlers/auth.go`、`api/route.go` | 实测：连续 16 次错误密码 → `401×8` 后转 `429`（修复前 16 次全 401、无限制） |
| E3 | AI Service 内部认证默认 **fail-closed**（新增 `AI_SERVICE_ALLOW_ANONYMOUS` 显式退出口）、默认绑定 `127.0.0.1`、生产关闭 `reload` | `ai-service/main.py`、`.env.example` | 实测：无令牌 → **401**；带令牌 → **200**；`/health` 仍公开 200 |
| E4 | `/metrics` 加访问保护（`METRICS_TOKEN` 优先，无令牌时仅允许回环；生产未配置则启动失败） | `api/observability.go`、`cmd/server/main.go` | 新增 6 个单测全绿；实测回环 200、远程 401 且不泄露指标 |
| E5 | `isPlanningAdmin` 拒绝空/空白名单（修复 `Split("",",")→[""]` 的 fail-open）；模型端点新增 SSRF denylist（回环/私网/链路本地/云元数据，含 DNS 解析后校验） | `handlers/model_registry.go` | 新增单测：空名单不授权任何人；9 类内网地址被拒、公网 IP 通过 |
| E6 | `UpdateProfileHandler` 的 `user_id` 由 `binding:"required"` 改为可选并由会话推导（附不匹配 403）；所有响应补 `code` 字段；碳足迹校验失败由 **200+error** 改为 **422** | `handlers/http_api.go`、`api/carbon_service.py` | 实测：不带 `user_id` → **200 且写入成功**（修复前 400 空响应）；不匹配 → 403；`segments` 非法 → 422 |
| E7 | 删除 **18 个零导入死文件（3,229 行）** + `ContextualLobby.tsx` 内 3 处零渲染定义（992 行）；新增 `app/error.tsx` 错误边界与 `app/not-found.tsx` | `front/app/**` | 前端源码 **19,139 → 15,074 行（-21.2%）**；`useState` 75→51；`tsc` 0 error；生产构建成功；71 单测全绿 |
| E8 | `r.Run()` → 显式 `http.Server`（`ReadHeaderTimeout 10s`/`ReadTimeout 60s`/`WriteTimeout 120s`/`IdleTimeout 120s`/`MaxHeaderBytes 1MB`）+ `signal.NotifyContext` 优雅停机（15s 排水）+ 生产 `gin.ReleaseMode` + `gin.New()` 避免重复日志；绑定地址改为 `BIND_HOST` 可配（默认 `0.0.0.0`）并**如实打印真实监听地址** | `cmd/server/main.go` | `go build`/`go vet` 通过；实测优雅停机日志正常 |
| E9 | `git rm --cached` 停止跟踪 `gateway/gateway.exe`(53.9MB)、`gateway/omniroute.db`、`gateway/cmd/server/omniroute.db`、`front/tsconfig.tsbuildinfo`（**文件保留在磁盘**，`.gitignore` 规则随后生效，已用 `git check-ignore -v` 验证） | Git 索引 | 跟踪文件 67 → 63；暂存删除 **63.9MB** blob |
| **额外** | **发现并修复一个评估阶段未发现的预存缺陷**：`REDIS_URL` 已配置但 Redis 不可达时，`AIRateLimitMiddleware`/`RoomRateLimitMiddleware`/`allowAgentNegotiation` 全部硬失败 —— 使**所有 AI 端点返回 503**。新增 `checkLimit()` 统一降级到进程内限流 | `handlers/ratelimit.go` | 实测：碳足迹端点 **503 → 200 / 32ms**；`briefing/safety`、`providers/health` 均 200 |

同时在 `gateway/.env` 与 `ai-service/.env` 中生成并写入**匹配的 64 位内部令牌**（两侧一致，已验证），否则 E3 的 fail-closed 默认值会切断 gateway→AI 调用链。

### 阶段一验证总览（全部实测）

| 套件 | 结果 |
|---|---|
| `go build ./...` | **exit 0** |
| `go vet ./...` | **exit 0** |
| `go test ./...` | **4 个包全部 ok**（新增 14 个安全回归测试：Redis 降级、账号锁定、失败衰减、限流、管理员空名单、SSRF、metrics 守卫） |
| Python `unittest` | **Ran 57 tests — OK** |
| 前端 `tsc --noEmit` | **0 error** |
| 前端单测 | **71 passed / 0 fail** |
| 前端生产构建 | **成功**（`next build`，路由 `/`、`/_not-found`、`/api/agent`、`/api/transport`） |
| 端到端真实浏览器旅程（自建，step-by-step 校验大厅/个人主页/社区/懒加载地图） | **PASS 17.7s，零 page error** |

### 一处必须如实说明的问题：仓库自带 E2E 用例不稳定

`front/e2e/core-flow.spec.ts`（161 行、20+ 步、45 秒预算）在本次修改后**未能稳定通过**，且**每次失败的步骤都不同**（先后观察到 `:94` 地图 shell、`:126` 雷达 dialog、`:113` 黑板关闭按钮、`:133` 全球情报按钮）。已做的排查与结论：

- **曾误以为修掉了该用例暴露的一个缺陷（特此澄清）**：排查期间一度判断 `:101` 的 `fill('ROOM-E2E')` 会被输入框 `maxLength=6` 截断为 `ROOM-E`。**经核对源码，该输入框并无 `maxLength` 属性**（`app/ContextualLobby.tsx` 中的 `join-room-code` 仅设置 `type/placeholder/value/onChange`），因此该判断不成立，`fill` 无需改动，**本人也未做此改动**。真正观察到的首个失败位于 `:94`（地图 shell 断言）。
- **自建的真实节奏用户旅程测试通过**（大厅 → 个人主页 → 社区 → 懒加载地图，17.7s，零 page error），说明**产品功能本身正常**。
- **已排除的嫌疑**：`error.tsx`/`not-found.tsx`（临时移除并重建后仍失败）、应用逻辑回归（被删函数均为不可达代码，`tsc` 与生产构建均通过）、后端干扰（停掉 gateway 与 AI 服务后仍失败）。
- **最可能原因**：无头 Chromium 下 WebGL `ReadPixels` 造成渲染主线程停顿（`front/debug.log` 早有 4 条 "GPU stall due to ReadPixels"，本次跑测亦复现），使 20+ 步连点在高频交互下出现点击无法完成；**加入 settle time 后测试总时长超出该用例 45 秒预算**，无法用此法自证。
- **处置**：**未**放宽该用例的断言或超时来伪造通过。建议后续将其拆分为多个小用例、为 WebGL 步骤显式等待 `canvas` 尺寸稳定，并适当提高超时预算。

**结论**：阶段一 9 项 + 1 项额外修复均已完成并通过上述验证；E2E 套件自身的稳定性问题（评估阶段已记录为 W1/W7 相关的脆弱性）建议作为独立任务跟进，不应与本次修复混同。

---

## 第二轮实施记录：前端 UX 与安全 8 项修复（2026-05-19 追加）

承接评估中 §3.3 与 §4.2 列出的前端缺陷，本轮修复 8 项。

| # | 缺陷（修复前） | 修复内容 | 验证结果 |
|---|---|---|---|
| F1 | **缺失成本被静默渲染为 ¥0**：`planQuality` 用 `/[\d,.]+/` 匹配哨兵字符串 `"暂无供应商数据"` 失败后默认 0，头部显示"估算 ¥0" | 成本解析区分三种情形：**有报价** / **显式 0 元** / **无报价**。无报价节点不计入合计并单独计数；仅当全部无报价时显示"价格待补充（N 个节点无报价）"，部分无报价时显示"· N 个未计价"。正则收紧为必须含 `¥/￥/元` 货币信号，避免把 `"2026年"` 误读为价格 | `tsc` 通过；生产构建通过 |
| F2 | **假进度**：`handleConfirmSync` 用固定 `setTimeout(…,1000)` 判定"就绪"，与 WebSocket 状态无关 | 改为由房间 socket 的 `onopen`/`onclose` 维护 `roomSocketReady`（state + ref）作为**权威就绪信号**；已就绪则立即进入，未就绪则轮询该信号，8 秒上限后显示真实错误 | **仓库自带 E2E 用例由"每次失败"变为 4/4 稳定通过，且由 21.3s 提速至 15–17s**（去掉无谓等待） |
| F3 | **硬编码兜底目的地**：`:1947/:2000` `targetCityInfo?.name \|\| '乌鲁木齐'`，静默把用户请求改写为乌鲁木齐并写入 prompt | 两处（单景点替换、生成 B 方案）改为城市缺失时**明确报错并中止**，不再替换目的地 | `tsc` 通过 |
| F4 | **无错误边界**：`global-error`/`loading`/`not-found` 均缺失，全库无 `componentDidCatch`，主组件任一异常即整站白屏 | 新增 `app/global-error.tsx`（自带 `<html>/<body>`，捕获根布局级异常）、`app/loading.tsx`（首屏 loading 态）。（`error.tsx`/`not-found.tsx` 已于第一轮新增。） | 构建产物新增 `/_not-found` 路由；构建通过 |
| F5 | **同一资源双轮询**：`WorldSafetyGlobe` 与 `RiskPushCenter` 各起 30 秒定时器打同一个 `/api/v1/risk/realtime`（该端点单次约 10.5s 上游开销） | 新增 `app/lib/riskSnapshot.ts`：按 `city+coordinate` 共享**同一 in-flight Promise** + 25s 复用窗口；手动刷新/切换 tab 走 `force` 绕过。失败或空响应**不写入缓存**，避免后续轮询"复用"到 null 而永不重试 | **新增 8 个单测全部通过**（含"3 个并发调用只产生 1 次 fetch"、"失败后下次重试"）。测试过程中发现并修掉了我自己引入的一个缺陷：非 200 响应会把 `{data:null}` 写进缓存从而抑制重试 |
| F6 | **React 19 不安全写法**：`TransportSearch.tsx` 在 `setFrom` 的 updater 内调用 `setTo`（StrictMode/并发渲染下 updater 可能被重复调用） | 改为先从闭包读取两个值，再分别调用两个独立 setter | `tsc` 通过 |
| F7 | **iframe sandbox 过度授权**：`allow-scripts` + `allow-same-origin` 作用于由用户/POI 文本拼接的外部搜索页 | 移除 `allow-same-origin`（使被嵌页面获得 opaque origin），保留 `allow-scripts/allow-popups/allow-forms` 并补 `allow-popups-to-escape-sandbox` | 构建通过；E2E 通过 |
| F8 | **无 CSP / 无安全响应头**，而 `layout.tsx` 有两段内联脚本且无 nonce | `next.config.js` 新增 `headers()`：CSP、`X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`、`Referrer-Policy`、`Permissions-Policy`、`X-DNS-Prefetch-Control`；另对 `/sw.js` 加 `no-store` 与 `Service-Worker-Allowed` | **实测响应头已生效**（见下）；**CSP 零违规**：MapLibre 地图、DiceBear 头像、瓦片与字体在真实浏览器中均正常加载 |

### 关于 CSP 的一处诚实说明

`script-src` **仍包含 `'unsafe-inline'`**，原因写在 `next.config.js` 注释里：`app/layout.tsx` 有两段 `dangerouslySetInnerHTML` 内联引导脚本（主题初始化、Service Worker 注册），且 App Router 会输出水合所需的内联数据。要移除 `unsafe-inline` 必须引入 `middleware.ts` 为每个请求签发 nonce，并把这条路由改为动态渲染——这对本项目单一入口路由意味着放弃静态化。**这是有意识的后续项，不是遗漏**：策略中其余部分（无第三方 script 源、`object-src 'none'`、`base-uri 'self'`、`frame-ancestors 'none'`、connect/frame/img 源白名单）均已实际生效。

### 第二轮验证总览（全部实测）

| 套件 | 结果 |
|---|---|
| `go build` / `go vet` | exit 0 / exit 0 |
| `go test ./...` | 4 个包全部 ok |
| Python `unittest` | Ran 57 tests — OK |
| 前端 `tsc --noEmit` | 0 error |
| 前端单测 | **79 passed / 0 fail**（新增 8 个） |
| 前端 lint | passed |
| 生产构建 `next build` | 成功 |
| **仓库自带 E2E（`core-flow.spec.ts`）** | **4/4 PASS，15.4 / 16.1 / 15.5 / 16.8 秒** |
| CSP 违规监测（真实浏览器） | **0 条** |

### 一个需要修正的此前结论

第一轮记录中我曾判断"仓库自带 E2E 用例不稳定、每次失败在不同步骤，最可能原因是 WebGL 渲染停顿，与本次修改无关"。**该判断不完整**：在本轮修复假进度门控（F2）之后，同一个用例**连续 4 次全部通过且提速约 28%**。这说明先前的失败至少部分来自应用侧的竞态（工作区入口依赖固定 1 秒定时器而非真实就绪信号），而非纯粹的浏览器渲染停顿。特此更正。

---

## 第三轮实施记录：多智能体与持续学习闭环升级（2026-05-19 追加）

评估中判定「多智能体未成立、持续学习闭环断裂」后，按四步规划实施升级。

### 第一步：打通 bandit arm id 传递闭环

| 修改 | 文件 | 说明 |
|---|---|---|
| 规划结果新增 `bandit` 字段 | `ai-service/api/agent.py` | 选臂后回传 `{arm_id, variant_id, reason, explored, propensity, candidate_count}`，让客户端能把后续奖励归属到具体臂 |
| 前端捕获并回传 | `front/app/ContextualLobby.tsx` | 从 WS 的 `[FINAL_JSON]` 提取 `finalData.bandit.arm_id` 存入 state，传入 `<SatisfactionModal banditArmId={…}>`；bandit 关闭时清空，避免把奖励错记到上一轮 |
| 空 arm id 不再静默丢弃 | `gateway/internal/handlers/recommendation.go` | 空 id 时计数并限频告警（原实现直接 `return`，学习失败完全不可见） |

### 第二步：让学习真正有效（`core/contextual_bandit.py` 重写）

| 缺陷（修复前） | 修复后 |
|---|---|
| **探索全程只发生 1 次**：`explorations < max(1, int(decisions * budget))` 恒等于 `< 1` | 改为 Thompson 采样天然探索 + 全局**非贪心选择率上限**（按比率而非累计计数） |
| **名不副实的 Thompson**：只用后验均值 `α/(α+β)`，无 Beta 采样 | 实现真实 **Beta(α,β) 采样取 argmax**；`propensity` 由 64 次采样胜率估计，可用于离线评估 |
| **臂身份无意义**：用位置型 variant id（`variant_1`），跨发布后证据会挂到不同风格上 | 引入 `stable_arm_id(model, style)` → `qwen-plus:relaxed` 这类语义化、可跨发布复用的臂 |
| **`_arms` 无界增长** | 加 `max_arms=512` 容量上限，按 (pulls, α+β) 升序淘汰证据最弱的臂 |
| **状态文件非原子写** | 改为写临时文件 + `os.replace` 原子替换 |
| **首次运行永不持久化**：`load()` 仅在文件已存在时才记录保存路径 | 新增 `arm_state_file()`，首次运行即写初始快照并启用自动保存 |

**实测证据（真实服务，非单测）**

```
启动：algorithm=thompson_sampling_beta, rewards_recorded=0, arms=1
记录 qwen-plus:relaxed satisfaction=5 → reward=1.0
记录 qwen-plus:intense satisfaction=2 → reward=0.4
记录 qwen-plus:relaxed satisfaction=4 → reward=0.8
后验：relaxed 0.500→0.700 (pulls=2)；intense 0.500→0.467 (pulls=1)
状态文件 bandit_state.json 已生成（669 字节）
空 arm_id → HTTP 400（正确拒绝）
重启 AI 服务 → 后验完整恢复，日志：♻️ [Bandit] 已从 bandit_state.json 恢复学习状态
```

`bandit_state.json` **在本次升级前从未存在过**——这正是学习闭环从未真正运行的独立佐证。

### 第三步：让学习成果可见

| 修改 | 说明 |
|---|---|
| `/planning/quality` 新增 `learning` 字段 | 返回 `feedback_counters`（sent/accepted/rejected/**missing_arm**）+ 策略后验快照；`missing_arm > 0` 时附带告警文案 |
| `/metrics` 新增 `bandit_feedback` | 学习闭环健康度可直接抓取（为此新增叶子包 `gateway/internal/metrics`，绕开 api↔handlers 的导入环） |
| 工作区质量摘要新增策略徽标 | 显示「策略择优 / 策略探索 + 风格名」，悬停显示被选中概率 |
| **离线评估脚本** `ai-service/tools/evaluate_bandit.py` | 用 `planning_events` + `trip_satisfactions` 重建显式奖励，输出每臂后验、最优臂相对基线的提升与两比例 z 检验 |

**脚本在真实数据上的输出（诚实的负面结果）**

```
rated_outcomes=20  distinct_arms=1
arms: builtin (pulls=20, mean_reward=0.81)
verdict=insufficient_evidence  (exit code 2)
说明: 需要至少 30 条显式奖励且覆盖至少 2 个臂
```

脚本刻意**拒绝编造结论**：证据不足时输出 `insufficient_evidence` 并以退出码 2 结束，而不是从 20 条单臂数据里"算出"一个提升。历史数据中的 20 条奖励全部指向遗留臂 `builtin`（旧格式），新流程产生的 `model:style` 臂尚无足够样本。

### 第三步附带修复的一处真实缺陷

`gateway/internal/handlers/recommendation.go` 的 `FunnelMetricsHandler` 使用了**未声明的 `impression` 变量**（漏斗第一阶段曝光量无处计算）。已改为对 `RecommendationEvent` 全表去重用户数，与"每次事件都是一次曝光"的语义一致。

### ⚠️ 一处必须披露的自身事故

在改动 `recommendation.go` 时，我用 PowerShell `Set-Content` 回写文件，导致**编码被破坏**：32 处中文注释/字符串的末字节被替换为 U+FFFD，且 12 个函数签名与若干 `if` / `var` 声明因换行被吞而丢失。

- **发现方式**：`go build` 报 `invalid UTF-8 encoding` 与 `non-declaration statement outside function body`。
- **修复方式**：三轮脚本化修复——先按损坏映射逐行还原文本，再按注释锚点补回被吞的声明，最后补齐 3 处丢失的变量/结构体定义；全部以 **UTF-8 无 BOM** 写回并做严格解码校验。
- **完整性验证**（与 `git show HEAD:` 对比）：`functions lost: []`、`string codes lost: []`、`endpoints lost: []`，即**未丢失任何 HEAD 中存在的功能**。
- **教训**：在 Windows 上编辑含中文的 Go 源文件，不得使用 `Set-Content` / `Out-File` 等默认走控制台代码页的 cmdlet；本次之后所有文件改动均改用 edit 工具或 Python 显式 UTF-8 写入。

### 第三轮验证总览（全部实测）

| 套件 | 结果 |
|---|---|
| `go build` / `go vet` | exit 0 / exit 0 |
| `go test ./...` | 4 个包全部 ok |
| Python `unittest` | **Ran 78 tests — OK**（较第二轮 57 增加 21 个 bandit 测试） |
| `test_contextual_bandit.py` 独立运行 | **24 passed**（修复前该文件 `ModuleNotFoundError` 无法独立运行） |
| 前端 `tsc` | 0 error |
| 前端单测 | 79 passed / 0 fail |
| 前端生产构建 | 成功 |
| **仓库自带 E2E** | **2/2 PASS（15.8s / 17.1s）** |
| **真实学习闭环** | 奖励 → 后验 → 持久化 → **重启恢复** 全链路实测通过 |

---

## 第四步评估：真多智能体的规模与可行性

评估「把角色扮演改造成真正多智能体」的成本，基于实测事实：

**现状事实**
- 每次协商只有 **2 处 LLM 调用**（主调用 + 缺失天数修复），二者是同一智能体的首选/降级路径，非并发智能体。
- 智能体身份是 `agent.py:1526-1528` 硬编码的三元组（Felix/Alice/Bob），全部塞进**同一个 75 行 system prompt**。
- `schemas.py` 中的 `AgentProposal` / `RoundtableResponse` 是**完整定义但零调用**的数据模型——重构的骨架已经预留，但从未接线。
- 无轮次概念：`negotiation_log` 是让模型一次性"编出"的对话文本。

**改造方案与成本**

| 步骤 | 内容 | 工作量 |
|---|---|---|
| 1 | 把硬编码 personas 换成真实成员驱动（按 `room_members` 的 role/intent 生成 N 个智能体），每个智能体独立调用 LLM 产出 `AgentProposal`（模型已存在，直接复用） | 3–5 天 |
| 2 | 引入**轮次仲裁**：一个 arbiter 智能体接收各 proposal + 真实 `fairness_report` 效用，产出修正意见；循环 2–3 轮直至分歧收敛 | 5–8 天 |
| 3 | 并发调用与预算控制：N 个智能体并发 + 每轮 token/时间预算 + 分歧度量（效用标准差，沿用 `PLANNING_FAIRNESS_MAX_STD`） | 4–6 天 |
| 4 | 把轮次过程持久化为可审计轨迹，并作为 bandit 的额外上下文特征（成员数、分歧度、轮次数） | 3–4 天 |
| 5 | 回归保障：同一输入下多智能体与单次调用的质量对比评估（复用离线评估脚本框架） | 3–4 天 |

**合计约 18–27 人天**，且会让每次协商的 LLM 成本上升约 **N 倍**（N = 智能体数 × 轮次）。这正是「建议二 LLM 成本与延迟治理网关」应当先行的原因：**在没有 token 预算与分级路由之前铺开多智能体，成本会线性放大而缺少刹车**。

**建议顺序**：先完成建议二的成本治理（1 人月），再做真多智能体（3.5–5.5 人月）。本次已完成的学习闭环为其提供了必要的评估基础设施——没有臂级奖励与离线评估，多智能体的"哪套协作策略更好"同样无法验证。

---

## 0. 执行摘要

### 0.1 一句话结论

OmniRoute 是一个**功能广度远超其工程成熟度**的多智能体旅行规划平台：它已经实现了一套完整可运行的「行前规划 → 行程执行 → 行中风控 → 行后复盘」闭环，算法层有真实内容（Pareto 前沿、maximin 公平性、DBSCAN+TSP 路径优化、五维风险复合、碳足迹核算），三层服务均可独立启动、测试全绿；但它当前**不能以现状交付生产**——存在 5 项高危配置/加固缺陷，其中 2 项可直接导致认证系统完全不可用或凭证泄露。

### 0.2 关键量化指标（实测）

| 维度 | 指标 | 实测值 |
|---|---|---|
| 规模 | 源码总行数 | **35,447 行** |
| 规模 | Go / Python / TS·TSX | 12,524 / 5,265 / 17,658 行 |
| 规模 | 最大单文件 | `ContextualLobby.tsx` **4,760 行 / 257 KB，内含 25 个组件定义** |
| 接口 | Gateway 路由数 | **92 个**（含 4 个公开健康/指标端点） |
| 接口 | AI Service 端点 | **12 个** |
| 测试 | Go 测试 | **全部通过**（4 个包 ok，含 `go vet`） |
| 测试 | Python 测试 | **57 passed / 0.158s** |
| 测试 | 前端单测 | **71 passed / 0.57s**（但仅覆盖 `app/lib/*` 纯函数，UI 组件 0 测试） |
| 测试 | TypeScript | **0 error** |
| 测试 | Playwright E2E | **1 passed / 21.3s** |
| 性能 | 前端首屏 JS | **1,096.1 KB / 11 个 chunk**（另有 1,905 KB 与 1,008 KB 两个 chunk 为懒加载，**不在首屏**） |
| 规模 | 前端未达代码（dead code） | **约 4,238 行 ≈ 前端 22.1%** |
| 性能 | `/api/v1/risk/realtime` P50 | **10,563 ms**（3 次重复无缓存） |
| 性能 | 认证接口（Redis 正常） | 登录 **95 ms** |
| 安全 | 12 次连续失败登录耗时 | **932 ms，无任何限流** |
| 版本控制 | 工作树源码文件被 Git 跟踪比例 | **47 / 170 = 27.7%** |
| 版本控制 | 仓库体积 | **62.3 MB**（单文件 `gateway.exe` 53.9 MB） |

### 0.3 五条最重要结论

1. **P0｜认证系统在 Redis 缺失时整体不可用**：实测 `POST /api/auth/register` 与 `/login` 均返回 **HTTP 503**，等待 ~1.9s 后失败。任何 Redis 抖动 = 全站无法登录。（`gateway/internal/handlers/auth.go:39-51, 98-115`）
2. **P0｜Git 历史中存在真实用户凭证**：提交于 HEAD 的 `gateway/omniroute.db`（126,976 B）是可解析的 SQLite 库，含 **3 个用户账号 + bcrypt 哈希**（`admin`、`admin1`、`testuser1`）、17 条行程、4 条聊天记录。`users` 表无唯一索引，`Username` 仅 `not null`。
3. **P0｜登录接口零限流**：实测 12 次暴破 932ms 全返回 401，无 429、无锁定。按 bcrypt cost 10（服务端 ~70ms）推算，单账号约 **4.6 万次/小时** 在线猜测能力；同时 bcrypt 本身构成 CPU 耗尽向量。
4. **P1｜核心业务接口 10.5 秒且无缓存**：`/api/v1/risk/realtime` 因 `operations.py:61-74` 串行发起 3 个独立网络调用（实测 weather 1,782ms + traffic 998ms + safety_intel 8,355ms），且同城重复请求 3 次均为 10.5s —— **完全无缓存**。
5. **P1｜项目处于「未提交」状态**：170 个源码文件中仅 47 个被 Git 跟踪，**123 个（72.3%）无版本保护**；`gateway/internal/handlers/planning.go`(47KB)、`ai-service/api/risk_service.py`(29KB)、`front/app/ContextualLobby.tsx`(263KB) 等核心资产均未入库。

### 0.4 亮点速览（同等重要的正面结论）

- ✅ **三层架构边界清晰**，Gateway（Go/Gin）承担鉴权与编排、AI Service（FastAPI）承担算法与 LLM、前端（Next.js）承担交互，职责无混乱。
- ✅ **测试覆盖「叶子」扎实且真实通过**：本次复跑 Go 全绿、Python 57 例全绿、前端 71 例全绿、TS 零错误、E2E 真实浏览器主链路通过。
- ✅ **数据溯源纪律优秀**：服务端坚持「不臆造供应商数据」——`is_estimated` / `source` / `data_sources` 三件套贯穿天气、风险、碳足迹、POI，`LocalBaselineProvider` 明确拒绝伪造 HIGH 风险。
- ✅ **降级链设计是代码库最佳部分**：LLM 不可用时 `_synthesize_from_pool` 仍产出 `status: "degraded_fallback"` 的真实路线，四层兜底保证接口永不挂死。
- ✅ **若干安全细节做对了**：bcrypt(DefaultCost) 哈希、122-bit UUIDv4 不透明会话令牌、`hmac.compare_digest` 恒定时间比较、CORS 精确白名单且生产拒绝 `*`、头像上传路径穿越防护（UUID 文件名 + 魔数嗅探 + `filepath.Dir` 包含校验 + `O_EXCL`）、零 SQL 注入、零 RCE。

---

## 1. 评估方法与证据基础

| 手段 | 具体做法 | 产出 |
|---|---|---|
| 静态审计 | 全量阅读 Gateway/Python 核心模块，对 3 个超大文件（186KB/263KB/66KB）用 grep 建立符号地图后分块精读 | 路由表、数据模型、算法清单、缺陷定位（含 file:line） |
| 运行时实测 | 以 `gateway_dev.exe` 与 `uvicorn` 真实启动两个后端，用 PowerShell 发起真实 HTTP 请求 | 端到端功能验证、延迟分布、限流行为、错误码语义 |
| 对照实验 | 分别在「配置 REDIS_URL」与「移除 REDIS_URL」两种环境下启动 Gateway | 定位 P0 认证故障的确定性根因 |
| 测试复跑 | `go test ./...`、`unittest discover`、`node --test`、`tsc --noEmit`、`playwright test` | 5 套测试的真实通过状态 |
| Git 取证 | `git ls-files`、`git log --diff-filter=A`、`git cat-file -s`、`git show HEAD:...` 并用 sqlite3 解析提取的 blob | 未跟踪文件比例、提交物内容、凭证泄露证据 |
| 计量 | LOC、文件大小、hook 计数、构建 chunk 体积、进程内存 | 复杂度与性能瓶颈的量化依据 |

**评估局限（明确声明）**：未做渗透测试/模糊测试；未对 SQLite 中 bcrypt 哈希做离线破解尝试（仅确认算法与代价因子）；`run_negotiate`（1,740 行）的运行时行为未做真实 LLM 调用验证（需消耗付费额度）。

### 1.1 两处已修正的计量口径（保持诚实性）

评估过程中出现并已核实的两个计量偏差，特此记录，因为它们影响结论方向：

| 项 | 初判值 | 权威值 | 判定方法 | 结论影响 |
|---|---|---|---|---|
| `ContextualLobby.tsx` 行数 | 4,384 | **4,760** | .NET `ReadAllLines`（与 read 工具一致） | 严重度**上调**：单文件更大 |
| `ContextualLobby.tsx` 的 `useState` 数 | 75 | **75** | `ReadAllLines` 与 `Get-Content -Raw` 两种方法均为 75 | 维持原值；曾出现 128 的读数，实为把「hook 调用 + 跨组件累计」混算所致（该文件含 25 个组件定义，全文 75 处 `useState` 调用即全文件总量） |
| 前端首屏 JS | 1,905 + 1,008 KB | **1,096.1 KB / 11 chunk** | `.next/build-manifest.json` + `index.html` script 引用 | **结论方向反转**：该项由「缺陷」改为「优点」（详见 §3.2） |
| 前端未达代码 | 未识别 | **约 4,238 行 / 22.1%** | 逐文件 import 检索 + JSX 渲染次数统计（详见 §3.0） | 新增重大缺陷项 |

**更正说明**：本报告在初稿中把 1,905 KB 与 1,008 KB 两个 chunk 计为首屏负担，属于误判。经核验构建清单，二者均为 `next/dynamic` 懒加载产物（three.js 地球与 MapLibre 地图），**未出现在首屏 HTML 中**。这一点项目做对了，已在 §3.2 改正并记为优点。

---

## 2. 核心功能实现完整性

### 2.1 已核实的功能矩阵

Gateway 共 **92 条路由**，按业务域分布如下（实测自路由启动 banner 与 `route.go`）：

| 功能域 | 路由数 | 代表能力 | 实现评价 |
|---|---|---|---|
| 认证 | 3 | 注册 / 登录 / 登出 | 基础可用，**缺改密、重置、会话列表、吊销全部** |
| 用户资料 | 7 | 资料读改、头像上传、历史行程 | 完整，含路径穿越防护 |
| 社区 | 7 | 发帖 / 列表 / 评论 / 点赞 / 收藏 / 标签 | 完整但**存在 N+1（最多 ~2,000 次查询）** |
| 房间协同 | 3 | 建房 / 邀请码入房 / 房间 WebSocket | 完整，成员鉴权在每次升级时校验 |
| 行前规划 | 30+ | 上下文、数字孪生、目的地推荐、交通/住宿/餐饮、打包清单、次级行程、最小扰动重规划、知识图谱、多语种导览、AR 导览 | **广度罕见**，多数为启发式而非算法 |
| 行中执行 | 12 | 执行状态、旅程状态、应急预案、订单创建/取消、信号埋点、质量指标 | 完整 |
| 推荐闭环 | 8 | 用户画像、曝光/点击/采纳/满意漏斗、协同过滤、替换信号 | 完整，A/B 分流 `control/treatment` |
| 多人决策 | 5 | 方案保存/列表/投票/采纳/胜出 | 完整，一人一票由唯一索引保证 |
| 节点级协作 | 5 | 批注增/查/历史/解决/投票 | 完整 |
| 预算财务 | 7 | 预算设置、记账、**OCR 识别**、汇总、复盘、预测 | 完整 |
| 风险与碳 | 6 | 实时风险、订阅/退订/列表/检查、碳足迹、安全简报 | 完整，含降级链 |
| 模型治理 | 4 | 模型注册/列表/评估/晋升 | 完整，含 shadow/canary/active |
| 可观测 | 4 | `/ping` `/healthz` `/readyz` `/metrics` | 存在但 `/metrics` **未鉴权** |

**AI Service 12 个端点**：`conflict_detect`、`agent/negotiate`（NDJSON 流式）、`agent/bandit/feedback`、`agent/bandit`、`carbon/footprint`、`carbon/factors`、`amap/poi`、`risk/realtime`、`risk/subscriptions/refresh`、`events/stream`、`health`、`metrics`。

### 2.2 算法层的真实含量（区分「真算法」与「启发式」）

**真正实现的算法（有实证价值）**：

| 模块 | 算法 | 复杂度 | 证据 |
|---|---|---|---|
| `core/optimization.py:60-116` | 约束校验，返回带错误码的完整违规清单（非布尔） | O(n) | 单遍扫描 |
| `core/optimization.py:150-178` | Pareto 非支配排序，支持逐目标最大化/最小化 | O(n²·k) | 语义正确 |
| `core/optimization.py:181-217` | 加权效用 + **maximin 公平选择** + 方差/后悔度破平 | O(n·m) | 公平性有理论依据 |
| `core/constraints.py:293-382` | 四级约束层级 + 硬过滤 → Pareto → 可解释排序 | O(c·n + c²) | 真实流水线 |
| `api/agent.py:636-684` | **DBSCAN 地理聚类**（haversine/ball_tree）+ 完全图 + **Christofides TSP** | O(n²) 建边 | 真实运筹 |
| `api/risk_service.py:238-266` | 五维加权复合 CII + 逐维贡献分解 | O(1) | 可解释 |
| `api/risk_service.py:361-443` | 四级风险变更检测（等级跳变/CIIΔ≥15/天气/拥堵/新告警） | O(k) | 真实 diff |
| `api/carbon_service.py:85-164` | 距离×因子核算 + 绿色替代方案减排量 + 树木当量 | O(n) | 真实算术，来源公开 |
| `core/contextual_bandit.py:35-79` | 显式奖励门控（**拒绝把曝光/点击当正反馈**） | O(1) | 方法论正确 |

**名不副实或启发式的部分（需诚实标注）**：

| 位置 | 命名/宣称 | 实际实现 | 影响 |
|---|---|---|---|
| `agent.py:585-626` | `NashEquilibriumSolver` | **无任何纳什均衡计算**，委托 Pareto | 命名误导（代码注释已自认） |
| `agent.py:796-850` | `TopologyFitnessCalculator` | 加权线性启发式 + 硬编码魔法数 | 非「拓扑适应度」 |
| `core/contextual_bandit.py:197-203` | `ContextualThompsonBandit` | **无 Beta 采样**，仅后验均值 | 非 Thompson 采样 |
| `core/contextual_bandit.py:124` | 探索预算 10% | `max(1, int(decisions*0.10))` → 全程**恰好 1 次探索** | 无法学习 |
| `core/time_intelligence.py` | 时间智能 | 7+8+8 个关键词查表，**不读真实营业时间/星期** | 非时间智能 |
| `travel_utils.py:57-64` | `estimate_crowdedness` | `percent` 固定 82/58/35，与其 `score` **无关**（score=123 仍 82%） | 展示伪精确数字 |
| `agent.py:2108-2112` | `team_satisfaction` | **硬编码 96/94/93** 写进 LLM 输出模板令其回显 | 伪造用户可见指标 |
| `agent.py:2366` | 降级路径满意度 | 硬编码 `88` | 同上 |

### 2.3 完整性缺口（缺失的关键能力）

| 缺失能力 | 说明 | 严重度 |
|---|---|---|
| **密码修改 / 重置** | 全库无相关端点，用户无法轮换凭证；令牌泄露后 24h 内无法自救 | 高 |
| **会话管理** | 无「吊销全部会话」「活跃设备列表」，`revokeSession` 仅删单个 token | 中 |
| **刷新令牌** | `sessionTTL = 24h` 硬编码绝对过期，无滑动续期，体验上表现为「用着用着被登出」 | 中 |
| **统一请求校验** | Python 12 个端点中 **10 个**用 `Dict[str, Any]` 裸接收，字段校验全靠手写；仅 1 个声明 `response_model` | 中 |
| **可部署产物** | 全库无 `Dockerfile` / `docker-compose` / systemd unit / K8s manifest / `Procfile` / 部署 workflow | 高 |
| **优雅停机** | `main.go:78` `r.Run()` 无 `signal.Notify` / `Shutdown`，重启即切断所有 WebSocket 与 300s AI 流 | 高 |
| **服务器超时** | 无 `ReadTimeout`/`WriteTimeout`/`IdleTimeout`/`ReadHeaderTimeout` → 慢速攻击可无限占用 goroutine | 高 |
| **数据保留策略** | `messages` / `planning_events` / `recommendation_events` / `feedback_logs` 全表无 TTL、无归档、无清理任务 | 中 |
| **外键约束** | 全模型无 `foreignKey`/`constraint` tag，且未开启 `PRAGMA foreign_keys=ON`，孤儿行永远可写入 | 中 |
| **审计日志** | 登录失败、越权尝试（`USER_MISMATCH`）、限流触发**均无日志**，暴破活动零痕迹 | 中 |

---

## 3. 用户体验流畅度

### 3.0 前端最大缺陷：22.1% 的代码永不执行（实测逐文件验证）

对每个可疑模块做「谁 import 了它」的全树检索，结果如下（**实测**）：

**A. 完全死文件 —— 零导入者（18 个文件，3,289 行）**

| 文件 | 行数 | 说明 |
|---|---|---|
| `components/UnifiedWorkspace.tsx` | 967 | **完整工作区第二实现**，与线上工作区功能重复 |
| `components/MafengwoStylePanel.tsx` | 479 | 仅被同样已死的 `UnifiedWorkspace` 导入 |
| `components/AuthPortalScreen.tsx` | 230 | 登录门户第二实现 |
| `components/AuthPage.tsx` | 225 | 登录页第三实现 |
| `components/TeamPresenceBar.tsx` | 216 | 仅被已死的 `MafengwoStylePanel` 导入 |
| `components/PlanVariantBar.tsx` | 198 | 仅被已死的 `UnifiedWorkspace` 导入 |
| `components/ExpandableConnectorNav.tsx` | 166 | 仅被已死的 `MafengwoStylePanel` 导入 |
| `components/DeductionPanel.tsx` | 157 | 仅被已死的 `UnifiedWorkspace` 导入 |
| `components/NodeAnnotationPanel.tsx` | 123 | 零导入者（且内含第 4 个独立 WebSocket 连接） |
| `components/DraftingPanel.tsx` | 102 | 仅被已死的 `UnifiedWorkspace` 导入 |
| `components/WeatherCrowdPanel.tsx` | 101 | 零导入者 |
| `components/BottomMapCarousel.tsx` | 82 | 仅被已死的 `UnifiedWorkspace` 导入 |
| `components/DynamicBudgetCard.tsx` | 56 | 仅被已死的 `MafengwoStylePanel` 导入 |
| `components/CollaborativeRecommend.tsx` | 49 | 仅被已死的 `UnifiedWorkspace` 导入 |
| `components/EditIntentModal.tsx` | 41 | 零导入者 |
| `components/TravelModeCard.tsx` | 17 | 零导入者 |
| `components/PreferenceCard.tsx` | 15 | 零导入者 |
| `components/communityscreen.tsx` | 5 | 重导出 shim，零导入者 |

**B. `ContextualLobby.tsx` 内部的死代码（约 949 行）**

| 定义 | 行范围 | JSX 渲染次数（实测） | 对应活代码 |
|---|---|---|---|
| 本地 `ProfileScreen` | `:619-1139`（521 行） | **0** | 活的实为 `components/ProfileScreen.tsx`（`:1991` 以 `ModernProfileScreen` 之名渲染） |
| 本地 `CommunityScreen` | `:1165-1631`（467 行） | **0** | 活的实为 `components/CommunityPanel.tsx`（`:2006` 渲染） |
| 本地 `predictBudget` | `:67-75` | 仅定义，无调用 | 活的实为 `lib/utils.tsx:137` |

**C. 逻辑重复（漂移的多份拷贝）**

- 26 字段的路由映射对象在 `ContextualLobby.tsx` 中**逐字重复 3 次**（`:2431-2456`、`:2514-2539`、`:2576-2601`）；
- `normalizeLnglat` 在两个模块中**字节级相同**（`ContextualLobby.tsx:167-190` vs `lib/utils.tsx:200-222`）；
- 社区流实现 **3 份**、发帖 **4 份**、认证表单 **3 份**、`POST /api/v1/planning/events` 埋点 **11 处**、`GET /api/v1/budget/predict` **2 份完全相同**；
- `ErrorState` 组件 2 份（`CommunityPanel.tsx:117`、`ProfileScreen.tsx:168`）。

**结论**：约 **4,238 行 / 前端 22.1%** 为死代码或重复实现。这不是「未清理」这么简单——它意味着任何 bug 修复都可能只落在一份拷贝上，而评审者需要为永不运行的代码付出成本。**建议在阶段一直接删除 A 类全部 18 个文件与 B 类 3 处定义**，这是零风险的净收益。

**判定方法说明（回应「这不是未使用的工具函数吗」）**：判定标准不是「看起来没用」，而是两条硬证据：① **import 图**——用全树正则检索 `from '...名称'` 与 `import('...名称')`，A 类 18 个文件的结果是**零命中**；② **JSX 渲染点**——对 B 类，统计 `<组件名` 在文件内的出现次数，结果均为 **0**。两者都不是启发式判断，而是可复现的字符串事实。

**实测对照（同类功能的两套完整实现）**

| | 活的那套 | 死的那套 |
|---|---|---|
| `ProfileScreen` 定义位置 | `ContextualLobby.tsx:619-1139`（本地函数） | `components/ProfileScreen.tsx`（独立文件） |
| 导入方式 | 同文件内直接定义 | `ContextualLobby.tsx:24` `import ModernProfileScreen from './components/ProfileScreen'` |
| 渲染点 | `ContextualLobby.tsx:1991` `if (showProfile) { … <ModernProfileScreen …` → **渲染的就是本地那份** | 导入的组件被渲染，但**组件体内引用不到本地定义** |
| 调用的后端接口 | `/api/community/list`、`/api/community/post`、`/api/user/avatar`、**`/api/user/profile/update`**、`/api/user/profile` | `/api/community/list`、`/api/community/post`、`/api/user/profile`、`/api/user/trips` |

**⚠️ 一处方向性更正（重要）**：本节初稿把这个关系写反了，称「死代码调用 `/api/user/profile/update`、活代码调用 `/api/user/trips`」。经对 `ContextualLobby.tsx:1991` 逐行核对，**实际相反**：

- 第 24 行 `import ModernProfileScreen from './components/ProfileScreen'` 只是把组件绑定到**变量名** `ModernProfileScreen`；
- 第 1991 行渲染 `<ModernProfileScreen>`，该组件的实现**来自 `components/ProfileScreen.tsx`**；
- 而第 619 行的本地 `function ProfileScreen` **从未被任何 JSX 引用**，是死代码。

因此：**`components/ProfileScreen.tsx`（调用 `/api/user/trips`）是活代码，`ContextualLobby.tsx:619-1139` 的本地 `ProfileScreen`（调用 `/api/user/profile/update`）才是死代码。**

**连带结论翻转**：D18「资料更新不可用」缺陷**确实可从 UI 触达**——因为活着的那份 UI（`components/ProfileScreen.tsx`）正是调用 `/api/user/profile/update` 的一方。初稿曾据错误方向推断该缺陷「当前未触发用户可见故障」，**该推断作废**，D18 严重度维持在「功能性缺陷」。（该缺陷已在阶段一修复，见 §13。）

### 3.1 前端复杂度实测（真实瓶颈所在）

| 组件 | 行数 | useState | useEffect | useMemo | useCallback | useRef |
|---|---|---|---|---|---|---|
| **`app/ContextualLobby.tsx`** | **4,760** | **75** | **17** | 10 | **1** | 1 |
| `app/components/UnifiedWorkspace.tsx` | 895 | 17 | 5 | 4 | 0 | 1 |
| `app/components/WorldSafetyGlobe.tsx` | 1,031 | 12 | 11 | 8 | 1 | 1 |
| `app/InteractiveAmapComponent.tsx` | 721 | 9 | 7 | 0 | 0 | 3 |
| `app/components/TransportSearch.tsx` | 599 | 9 | 0 | 1 | 6 | 0 |
| `app/FullRouteVisualizer.tsx` | 542 | 5 | 1 | 3 | 0 | 0 |
| `app/components/MafengwoStylePanel.tsx` | 445 | 3 | 0 | 0 | 0 | 0 |
| `app/DestinationMap.tsx` | 423 | 6 | 2 | 1 | 0 | 1 |
| `app/components/ProfileScreen.tsx` | 366 | 15 | 4 | 6 | 2 | 0 |
| `app/components/CommunityPanel.tsx` | 348 | 14 | 3 | 3 | 2 | 0 |

**问题诊断**：`ContextualLobby.tsx` 是整个应用的路由入口（`app/page.tsx` 仅 7 行，直接 `export default ContextualLobby`），单个 client component 承载 **75 个独立状态**、**1 个 useCallback**。这意味着：

- 任意一个 state 变化都会触发该 4,760 行组件的**全量重渲染**，且几乎无记忆化保护（75:1 的 state/memo 比例是极强的重渲染信号）。
- 全应用所有登录、房间、规划、地图、预算、社区逻辑被编译进**同一个 chunk**，无法按需加载。
- 该文件 263 KB 源码无法被团队并行维护，也无法被 `tsc`（`strict: false`）有效约束。

### 3.2 构建产物实测（含一处对我方初判的修正）

| 指标 | 实测值 | 评价 |
|---|---|---|
| `.next` 总体积 | **1,191.8 MB** | 含 cache；反映构建量级偏大 |
| **首屏 JS（`index.html` 内 11 个 chunk 合计）** | **1,096.1 KB** | 偏大但可接受（约 4.5 倍建议阈值） |
| 最大 JS chunk `19htmogjt6v77.js` | 1,905 KB | **懒加载**（three.js/react-globe），**不在首屏** |
| 第二大 chunk `261cq39jf3lqt.js` | 1,008 KB | **懒加载**（MapLibre/mapbox-gl），**不在首屏** |
| `rootMainFiles` 框架基础 | 446.1 KB | 正常 |
| 最大 CSS chunk | 154 KB | 尚可 |
| `static` 目录合计 | 4.24 MB | — |
| TypeScript `strict` | **false** | 类型守门大幅削弱 |

**修正说明**：本报告初稿曾将 1,905 KB 与 1,008 KB 计为首屏负担，经核验 `.next/build-manifest.json` 与 `index.html` 的 script 引用，二者均**未被首屏引用**。项目在这一点上**做对了**：`ContextualLobby.tsx:77, 91, 101` 使用 `next/dynamic(..., { ssr: false })` 正确地把 three.js 地球与 MapLibre 地图拆出首屏。

**仍存在的真实问题**：
- 首屏 1,096 KB 中包含**完整 framer-motion 运行时**（165 处 `motion.` 引用，无 `LazyMotion`/`m.` 拆分，仅用 121.6 KB 的独立 chunk 承载），而首屏仅用于登录页与大厅的基础过渡动画；
- 首屏 CSS（154 KB）**内嵌了完整 MapLibre 样式表**，但登录页不使用地图；
- `data/provinceData.ts`（326 行 / 30.6 KB）被编译进 **首屏 chunk** `0vllxnhx6d9bh.js`（36.8 KB，实测包含 `poyPreviews` 与 `北京市`），而它只被大厅的灵感卡片与懒加载的 `DestinationMap` 需要。

`package.json` 中 `react` / `react-dom` / `tailwindcss` / `postcss` / `autoprefixer` 五个依赖使用字面量 `latest`——**任意一次 `npm install` 都会把 React 静默升级到 Next 16 未曾测试的版本**。

### 3.3 交互与可用性问题（实测发现）

| 问题 | 证据 | 用户可感影响 |
|---|---|---|
| **接口返回空响应体** | 实测 `POST /api/user/profile/update` 与 `PUT /api/user/profile` 均返回 `400` 且 **body 为空** | 用户改资料失败时看不到任何原因，只能看到「点了没反应」 |
| **缺失成本被静默渲染为 ¥0** | 节点取 `cost: r.cost_estimate \|\| r.cost \|\| "暂无供应商数据"`（`:2442/2525/2587`）→ `planQuality` 用 `match(/[\d,.]+/)` 提取，非数字则 `0`（`:2407-2411`）→ 头部展示 `估算 ¥{estimatedCost}`（`:3086`） | **完全未定价的行程会对外宣称总价 ¥0**，且共识/质量指标基于同一份数据一并失真 |
| **假进度：固定 1s 定时器决定进入工作区** | `:1808-1814` 硬编码 `setTimeout(…, 1000)`，与房间/WebSocket 就绪状态无关 | 连接失败时用户仍会「顺利」进入工作区，随后才发现无数据 |
| **硬编码兜底目的地** | `:2939` 与 `:2992`：`targetCityInfo?.name \|\| '乌鲁木齐'` | 目的地缺失时**静默替换为乌鲁木齐**并写入用户可见 prompt |
| **无错误边界，单点异常白屏** | 实测 `app/error.tsx`、`global-error.tsx`、`not-found.tsx`、`loading.tsx`、`middleware.ts` **全部不存在**；全库无 `componentDidCatch`/`ErrorBoundary` | 4,760 行主组件内任何一处抛异常都会用 Next 默认错误页替换整个产品，用户丢失整个会话 |
| **状态更新器内含副作用（React 19 下不安全）** | `TransportSearch.tsx:75-80`：`setFrom(prev => { setTo(prev); return to; })` | StrictMode/并发渲染下更新器会被双调用，行为不可预测 |
| **同一资源被两个 30s 轮询器重复拉取** | `RiskPushCenter.tsx:171` 与 `WorldSafetyGlobe.tsx:363` 对同一 `POST /api/v1/risk/realtime` 各起一个 30 秒定时器 | 雷达打开时后端负载翻倍；叠加 §5 的 10.5s 单次延迟，压力显著 |
| **iframe sandbox 过度授权** | `:3504` `allow-scripts allow-same-origin allow-popups allow-forms`，`src` 由用户/POI 文本拼接的外部搜索 URL（`:3423-3430`） | `allow-scripts` + `allow-same-origin` 是经典反模式，仅因目标为跨域站点而危害受限；查询串仅 URL 编码未净化 |
| **无 CSP / 无安全响应头** | `next.config.js` 只有 `rewrites()`，无 `headers()`；而 `layout.tsx:61,67` 注入两段内联 `<script>` 且无 nonce | 无 CSP 可缓解 XSS，无 `frame-ancestors` 可防点击劫持（可与 localStorage 令牌叠加放大） |
| **`UpdateProfileHandler` 要求前端不可能提供的字段** | `http_api.go:434` `UserID string \`json:"user_id" binding:"required"\`` 强制必填，但该值本应从会话推导（`:443` 之后立即被 `req.UserID = userID` 覆盖） | 任何不显式传 `user_id` 的客户端**永远无法更新资料**——这是功能性缺陷而非仅体验问题 |
| **风险面板约 10.5 秒阻塞** | `/api/v1/risk/realtime` 三次实测 10,835 / 11,016 / 11,656 ms | 「实时风险推送」实为 10 秒级等待 |
| **无缓存导致重复付费** | 同城（Kyoto）连续 3 次请求分别 10,935 / 10,554 / 10,524 ms | 每次切城市都重新打全部上游 |
| **错误码语义错乱** | 碳足迹接口对非法输入返回 **HTTP 200 + `{"error": "segments 必填且非空"}`**（`carbon_service.py:178`） | 监控/重试/告警机制全部失效，前端也无法用状态码分流 |

**值得肯定的体验设计**：E2E 测试真实覆盖了「注册/登录 → 建房 → 入房 → AI 推演 → 预算 → 导出」主链路，并显式断言**无横向溢出**（`expectNoHorizontalOverflow` 校验 `scrollWidth <= clientWidth + 1`）与**无 React 生命周期错误**（监听 `synchronously unmount a root` / `React was already rendering`）。这属于相当成熟的移动端适配回归防护。同时存在 `public/sw.js` 与 `public/manifest.json`，具备 PWA 基础。

---

## 4. 安全性评估

### 4.1 已建立的安全基线（正面）

| 控制点 | 实现 | 证据 |
|---|---|---|
| 密码哈希 | bcrypt `DefaultCost`(=10) | `auth.go:302,352` |
| 会话令牌 | UUIDv4 = **122 bit** 不透明令牌（非 JWT，无签名密钥可泄露） | `auth.go:98-99` |
| 令牌撤销 | 支持按令牌吊销（登出） | `auth.go:148-162` |
| 内部服务认证 | `hmac.compare_digest` 恒定时间比较，生产缺失则 fail-closed 503 | `main.py:26-47` |
| CORS | 精确字符串匹配白名单，拒绝 `*`，`Vary: Origin` | `main.go:106-142`, `auth.go:55-69` |
| SQL 注入 | **零风险**：全部参数化，仅有的 `Raw/Exec` 均为字面量或 `?` 占位符 | `db.go:42-273` |
| 命令执行 | **零**：Go 无 `os/exec`，Python 无 `subprocess`/`eval`/`pickle.loads` | grep 全库无匹配 |
| 上传安全 | 5 MiB 硬限 + 魔数嗅探决定扩展名 + UUID 文件名 + `filepath.Dir` 包含校验 + `O_EXCL` | `http_api.go:350-429` |
| 越权防护 | `requireRoomMember` / `requireTripOwner` / `requireTripOrRoomAccess` 覆盖约 109 处调用；房间不存在与非成员统一 403 以避免房间枚举 | `authorization.go:27-185` |
| 身份冒用 | 中间件同时拦截 query 与 body 中的 `user_id`/`created_by` 不匹配 | `auth.go:201-225` |
| 日志脱敏 | 请求日志不含 header/query/body | `observability.go:85-94` |

### 4.2 高危发现（按可利用性排序）

#### S-1【高危】Redis 不可用导致认证系统完全瘫痪（实测复现）

```
环境：gateway/.env 设置 REDIS_URL=redis://localhost:6379/0，宿主无 Redis
实测：POST /api/auth/register  → HTTP 503（等待 ~1.9s）
      POST /api/auth/login     → HTTP 503
      GET  /api/user/profile   → HTTP 401
对照：移除 REDIS_URL 后 → 注册 248ms 成功、登录 95ms 成功
```

根因链：`newSessionToken`（`auth.go:98-115`）无条件尝试 `sessionRedis()`；`sessionRedis()`（`:76-96`）内部 `redisOnce.Do` 把首次拨号失败**永久缓存**进 `redisInitErr`；于是每次登录都先付 ~1.7s 连接重试代价，再返回 503。开发回退路径（in-memory `sessionStore`）**永远不可达**。

影响：任何 Redis 抖动/未启动/网络分区 = 全站无法登录与注册，且每次尝试额外消耗约 2 秒并放大 5 次拨号重试。这是「配置了可选依赖但未实现优雅降级」的典型故障。

#### S-2【高危】Git 历史含真实用户凭证与业务数据（实证）

```
git cat-file -s HEAD:gateway/omniroute.db   → 126976
提取 blob 并用 sqlite3 打开 → 9 张表，有效 SQLite
  users            rows=3    (testuser1 / admin / admin1，均为 $2a$10$ + 60 字符 bcrypt)
  trip_plans       rows=17
  messages         rows=4
  community_posts  rows=1
  comments         rows=1
```

工作树版本 `gateway/omniroute.db`（335,872 B）含 **19 个用户**、35 条行程。虽然 `.gitignore:34` 有 `*.db`，但**忽略规则对已跟踪文件无效**；`git ls-files` 仍列出 `gateway/omniroute.db`、`gateway/cmd/server/omniroute.db`、`gateway/gateway.exe`（HEAD blob **53.9 MB**）、`front/tsconfig.tsbuildinfo`。仓库 62.3 MB 中绝大部分即该二进制。

影响：任何克隆者获得 `admin`/`admin1` 账号的密码哈希；若口令为弱口令（用户名本身就强烈暗示），bcrypt cost 10 在 GPU 上并非不可攻破。用户行程与聊天内容同属隐私泄露。**需要重写历史，而非仅删除文件。**

#### S-3【高危】登录/注册接口零限流，可高速暴破（实测）

```
实测：对已知用户名连续 12 次错误密码
      → 401 ×12，耗时 932 ms，无 429、无锁定、无验证码
      → 约 13 次/秒 ≈ 4.6 万次/小时/账号
```

`route.go:23-24` 将 `/api/auth/register` 与 `/api/auth/login` 裸注册，**未挂任何限流中间件**（`AI/room` 限流器仅覆盖 8 条业务路由）。`LoginHandler`（`auth.go:347-350`）在用户不存在时提前返回，仅在用户存在时才执行 bcrypt —— 这既构成**时序侧信道**，也让 bcrypt(70ms) 成为 CPU 耗尽放大器（每次猜测都强制服务端做一次昂贵哈希）。

同时 `RegisterHandler:298` 返回 `409 用户名已被注册`，是**显式的用户名存在性预言机**（实测确认 409，body 为空）。

#### S-4【高危】AI Service 默认对所有网卡开放且内部认证默认关闭

```python
main.py:26-31  required = (env in {"production","prod"}) or AI_SERVICE_REQUIRE_INTERNAL_AUTH
main.py:65-67  uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
```

`ai-service/.env` **未设置 `APP_ENV`** → 默认 `development` → `required=False`；且 `AI_SERVICE_INTERNAL_TOKEN` 为空 → `hmac.compare_digest` 分支永不执行。结果：**除 `/health` 外全部端点（含消耗 LLM 额度的 `/agent/negotiate`）在 `0.0.0.0:8000` 无认证开放**，并以 `reload=True`（开发热重载）运行。实测 `POST /api/v1/carbon/footprint` 未携带任何令牌即返回 200，印证该判断。

#### S-5【中危】`/metrics` 未鉴权，泄露完整端点清单与流量

```
实测：GET http://127.0.0.1:8080/metrics  → HTTP 200（无 Authorization）
返回：requests_by_path 逐路径请求数与平均延迟 + requests_by_status + uptime
```

`main.go:66` 在 `api.RegisterRoutes(r)` 之前把 `/metrics` 注册在根引擎上，绕过了所有中间件。对攻击者而言，这是一份带流量权重的完整端点地图，侦察成本为零。

#### S-6【中危】实时凭证明文落盘（未提交，但工作树泄露面大）

| 文件 | 键 | 真实值前缀 | 长度 |
|---|---|---|---|
| `gateway/.env:15` / `ai-service/.env:5` | `AMAP_API_KEY` | `4108bb…` | 32（**两服务重复**） |
| `gateway/.env:27` | `ROLLINGGO_API_KEY` | `mcp_2f…` | 36 |
| `ai-service/.env:3` | `LLM_API_KEY`（DashScope/Qwen） | `sk-f30…` | 35 |
| `ai-service/.env:6` | `SENIVERSE_API_KEY` | `SZz_0z…` | 17 |

已确认这些文件**从未被提交**（`git log --all --diff-filter=A` 无 `.env` 路径，`.gitignore:7` 的裸 `.env` 规则在任意层级生效）。但工作树内存在约 4.5 GB 未跟踪运行时残骸（`work/runtime/*.exe` 五个约 68 MB 二进制、`work/npm-cache` 完整缓存、`work/runtime-logs/`），任何一次打包/拷贝/截图都可能连带泄露。此外 `PLANNING_DATASET_HASH_SALT` 为空，使数据集导出的「去标识化」HMAC 以空串为密钥，**可逆**。

#### S-7【中危】管理权限校验 fail-open（潜在提权）

```go
// model_registry.go:77-84
for _, candidate := range strings.Split(os.Getenv("PLANNING_ADMIN_USER_IDS"), ",") {
    if strings.TrimSpace(candidate) == userID && userID != "" { return true }
}
```

`PLANNING_ADMIN_USER_IDS` 为空时 `strings.Split("", ",")` 得到 `[""]`，`"" == ""` 成立，仅靠 `userID != ""` 兜住。今日安全（`AuthMiddleware` 拒绝空主体），但任何中间件重排或直接调用 handler（测试即如此）都会让**任意调用者成为规划管理员**——可注册任意 `Endpoint` 的模型（`:103`）、晋升为 active、并导出全量训练集（`planning_events.go:308`，limit 最高 10,000、窗口 730 天）。

#### S-8【中危】SSRF：模型端点为管理员可控的任意 URL

`model_registry.go:139-143` 仅校验 `http(s)://` 前缀与长度 ≤500，无内网/回环/元数据地址denylist。结合 S-7 的 fail-open，可让 Gateway 向 `http://169.254.169.254/...` 或 `http://127.0.0.1:<port>/` POST 攻击者影响的 JSON，用于云元数据窃取与内网端口扫描。

### 4.3 数据层安全与完整性

| 项 | 状态 | 后果 |
|---|---|---|
| `users.username` 唯一索引 | **缺失**（仅 `not null`，`db_models.go:10`） | `RegisterHandler:296-318` 是 check-then-create 且**无事务** → 并发注册同名可双成功，登录时 `First` 随机命中，造成**账号混淆**（可能登录进他人行程/房间/预算） |
| 外键约束 | **零**（无 tag，且未开 `PRAGMA foreign_keys=ON`） | 孤儿行永远可写入 |
| 事务使用 | 仅 `InitDB` 与 `Backup` 两处；**业务写操作零事务** | 建房是 `Create(Room)` → `Create(RoomMember)` 两次写，失败时 `database.DB.Delete(&room)` 的错误被丢弃（`http_api.go:93`），崩溃即产生无成员孤儿房间 |
| `RiskSubscription` 唯一索引 | 缺失（`db_models.go:265-272`） | 并发订阅同城可产生重复行，风险告警重复计数 |
| 连接池 | `SetMaxOpenConns` **从未设置**（`database/sql` 默认无限） | 单写者 WAL 下表现为连接抖动 + `SQLITE_BUSY`（仅 5s `busy_timeout` 缓冲），而非优雅背压 |
| GORM 日志 | `logger.Silent`（`db.go:75`） | 慢查询与迁移失败**全部不可见** |
| 静态加密 | 无 | SQLite 文件即明文业务数据 |

---

## 5. 可扩展性评估

### 5.1 已识别的主要瓶颈（含量化）

| # | 瓶颈 | 量化证据 | 扩展含义 |
|---|---|---|---|
| B1 | **`/api/v1/risk/realtime` 串行 3 次网络调用** | 实测 weather 1,782ms + traffic 998ms + safety_intel 8,355ms = **11,135ms**（接口实测 10,563ms），3 次重复完全一致 | 该端点是纯 `await` 串行（`operations.py:61-74`），本可 `asyncio.gather` 降至约 max(…)≈8.4s；且**零缓存**，同城重复请求全量重打上游。风险订阅刷新（`refresh_subscriptions:90-105`）对该代价做 **O(订阅城市数)** 循环放大 |
| B2 | **`/api/community/list` 潜在 ~2,000 次查询** | `http_api.go:527-535` 每帖 3 次 `Count`，`:547` 每帖再 1 次 `userBrief`，外层 `Limit(500)`（`:508`） | 单次认证请求即可打满唯一 SQLite 写者；同时在 Go 内存中全量排序而非 SQL 排序 |
| B3 | **`/api/community/tags` 无限制全表扫描** | `http_api.go:787-788` `database.DB.Find(&posts)` 无 limit，且在 Go 中聚合 | 帖子允许 4 MiB/条（`:615`），表增长后单请求即可 OOM |
| B4 | **SQLite 单写者 + 无连接池上限** | `db.go:75-84`；每帧聊天、每次 POI 点击、每次规划事件、每次记账均触发写 | 多人协同（房间/在线状态/批注/实时预算）在并发写下必然争用；**这是当前架构对多用户的硬天花板** |
| B5 | **前端 4,760 行单组件 + 1,096 KB 首屏**，另有 22.1% 死代码 | ReadAllLines 计量；首屏 chunk 实测 1,096.1 KB（三个懒加载 chunk 已正确拆分） | 首屏解析与执行成本偏高；任何状态变更全量重渲染；无法按路由拆包；死代码需持续评审 |
| B6 | **AI Service 全进程内状态无界增长** | `_api_cache` 为无上限 `Dict`（`agent.py:68`）；`SemanticItineraryCache._mem`/`._vec_index` 无 maxsize 且**每键存完整 embedding**（`:1388-1389`）；bandit `_arms` 每次 `select()` 注册（`contextual_bandit.py:118-119`） | 进程生命周期内内存单调增长；多 worker 部署会各自积累**分歧的学习状态** |
| B7 | **事件流桥接在 Redis 不可用时无限重连刷屏** | 实测 stderr 每约 3.7s 一轮 `XRead 失败，3s 后重连`，5 次拨号重试/轮 | 日志洪泛 + 无谓 CPU/网络；与 S-1 同源（把可选依赖当必需依赖） |
| B8 | **LLM 调用无并发上限/无 token 计量/无成本预算** | `agent.py:2321` `max_tokens=16384`；无 token accounting、无 per-request budget、无并发闸门 | 突发流量直接线性转化为 LLM 账单；`ws_handler.go` 仅做每房间单次协商互斥与 4 次/分钟/用户 |
| B9 | **依赖漂移破坏可复现构建** | `requirements.txt` 仅声明 8 个包，而代码实际 import **numpy / networkx / sklearn / redis** 未声明；前端 5 个 `latest`；CI 用 Go 1.24.x 而 `go.mod` 要求 1.26.2 | 全新环境 `pip install -r requirements.txt` 后 `import agent` 直接失败；CI 工具链 pin 失效 |
| B10 | **无水平扩展设计** | 会话与限流可走 Redis（设计已考虑），但 SQLite 与进程内 bandit/cache 状态使多副本无法共享；`gateway.pid` 手工 PID 文件表明无编排 | 只能垂直扩展单实例 |

### 5.2 扩展性正面因素

- Gateway 的业务逻辑与存储访问基本隔离在 `internal/service` 与 `internal/handlers`，迁移到 Postgres 的改动面相对可控（GORM 已抽象，仅 `db.go` 的 PRAGMA 与 raw SQL 需替换）。
- 速率限制器已具备 Redis 分支（`ratelimit.go:83-115`，含 SHA256 键脱敏、真实 TTL），**已为多副本限流做好准备**，只差开启。
- 会话存储同样已有 Redis 分支（`auth.go:98-162`），多副本共享会话的设计意图明确。
- 迁移机制是**手写版本化 + 单事务 + `schema_migrations` 表**（`db.go:23-104`），且 migration 2 记录了一次真实事故的修复（删除遗留全局唯一索引），比裸 `AutoMigrate` 稳健得多。
- 备份链路工程质量高：`wal_checkpoint(TRUNCATE)` → `VACUUM INTO`（不支持时回退物理拷贝）→ `PRAGMA integrity_check` 校验 → 保留期清理；恢复路径先校验 SQLite magic、写临时文件、fsync、复验、保留 `.before-restore-` 回滚副本（`db.go:108-280`）。

---

## 6. 优势与不足总结

### 6.1 核心优势（附证据）

| # | 优势 | 证据 |
|---|---|---|
| A1 | **功能广度确实罕见**：92 条路由覆盖行前/行中/行后、多人协同、预算财务（含 OCR）、风险与碳、模型治理与 A/B 实验 | `route.go:9-194` 全量清点 |
| A2 | **工程习惯良好**：5 套测试真实全绿（Go 全绿、Python 57、前端 71、TS 0 error、E2E 1 passed/21.3s），CI 含 test+vet+lint+typecheck+build+e2e 六道关卡 | 本次复跑实测 |
| A3 | **数据溯源纪律**：`source`/`is_estimated`/`data_sources` 贯穿，`LocalBaselineProvider` 明确拒绝伪造高风险，碳因子显式标注 `is_estimated: true` | `risk_service.py:173-199`、`carbon_service.py:161-163,197` |
| A4 | **降级链是代码库精品**：四层兜底保证 `run_negotiate` 永不挂死，降级时带 `status: "degraded_fallback"` 显式标记 | `agent.py:2352-2378, 2614-2624, 2699-2734, 2774-2873` |
| A5 | **安全细节多处做对**：bcrypt、122-bit 不透明令牌、恒定时间比较、CORS 精确白名单、上传路径穿越全防、零 SQLi、零 RCE、约 109 处越权校验 | 见 §4.1 |
| A6 | **备份/迁移工程质量高**：事务化版本化迁移（含真实事故修复记录）、`VACUUM INTO` + `integrity_check` + 回滚副本 | `db.go:23-104, 108-280` |
| A7 | **移动端回归防护意识**：E2E 显式断言无横向溢出与无 React 生命周期错误，并有 PWA manifest + SW | `e2e/core-flow.spec.ts:14-35`、`public/manifest.json` |
| A8 | **无 TODO/FIXME 债务标记**（全库 0 处），代码整洁度高于同类项目；Python 侧零裸 `except:` | grep 全库实证 |

### 6.2 核心不足（附证据与影响）

| # | 不足 | 证据 | 影响 |
|---|---|---|---|
| **D0** | **前端约 4,238 行（22.1%）为永不执行的死代码** | 18 个文件零导入者，已逐一实测（见 §3.0）；`ContextualLobby.tsx` 内另有 `ProfileScreen`(:619-1139)、`CommunityScreen`(:1165-1631)、`predictBudget`(:67-75) 三个本地定义**渲染次数为 0** | 同一功能存在 2–3 套实现（社区流 ×3、发帖 ×4、认证 ×3），修复只落在一份拷贝；死代码仍被编译进产物并需持续评审 |
| D1 | 认证在 Redis 缺失时**整体不可用** | 实测 503 ×2 | 全站不可登录 |
| D2 | Git 历史含**真实凭证与业务数据** | 解析 HEAD blob：3 用户 bcrypt 哈希 + 17 行程 | 隐私泄露 + 弱口令破解 |
| D3 | 登录**零限流** | 实测 12 次/932ms 无 429 | 高速暴破 + CPU DoS |
| D4 | AI Service **默认无认证且全网卡开放 + reload=True** | `main.py:26-31,65-67` + 实测无令牌 200 | 未授权 LLM 额度消耗 |
| D5 | **72.3% 源码未纳入版本控制** | 170 文件中仅 47 跟踪 | 单点磁盘故障即失代码 |
| D6 | 仓库含 **53.9 MB 二进制**与运行时数据库 | HEAD blob 计量；仓库 62.3 MB | 克隆成本 + 历史污染 |
| D7 | 核心接口 **10.5s 且无缓存**，多次独立调用串行 | 实测 3 次一致 10.5s；组件 1782+998+8355ms | 「实时」体验名不副实 |
| D8 | 社区列表 **~2,000 查询**；标签接口全表扫描 | `http_api.go:508,527-535,547,787-788` | 单请求打满单写者 |
| D9 | 前端 **4,760 行单组件 / 75 useState / 25 组件定义**，且 22.1% 为死代码 | ReadAllLines 计量 + 逐文件 import 检索（§3.0） | 全量重渲染、首屏 1,096 KB、不可维护、修复易落错拷贝 |
| D10 | **伪造用户可见指标** | `team_satisfaction` 96/94/93 硬编码；`estimate_crowdedness` percent 固定 | 信任与合规风险 |
| D11 | 算法**命名与实际不符**（Nash/Thompson/时间智能） | `agent.py:585-626`、`contextual_bandit.py:197-203`、`time_intelligence.py` | 技术叙事与实现脱节 |
| D12 | 核心 1,740 行 `run_negotiate` **零测试**；另有 3 处 `self` 未定义的潜在 NameError 被宽泛 `except` 吞掉 | `agent.py:2160,2527,2651`；测试覆盖率分析 | 核心路径回归无法发现 |
| D13 | 无部署产物、无优雅停机、无服务器超时 | 全库无 Dockerfile/systemd；`main.go:78` | 不可运维、慢速攻击可 DoS |
| D14 | 依赖漂移：Python 4 个未声明依赖、无锁文件；前端 5 个 `latest`；CI 工具链 pin 错版本 | `requirements.txt`、`package.json`、`ci.yml:17` vs `go.mod:3` | 构建不可复现 |
| D15 | 无外键、业务写无事务、`username` 无唯一索引 | `db_models.go` 全模型；`http_api.go:70-96` | 数据完整性无保障、账号混淆 |
| D16 | 无审计日志（登录失败/越权/限流触发均不留痕） | `auth.go:348,353` 无 log 语句 | 安全事件不可追溯 |
| D17 | 错误码语义错乱（校验失败返回 200） | 碳接口实测；`carbon_service.py:178` | 可观测性与前端分流失效 |
| D18 | `UpdateProfileHandler` 要求 `user_id` 必填却立即覆盖 → 资料更新功能**实际不可用** | `http_api.go:434` vs `:447` | 功能性缺陷 |
| D19 | Lint 是 38 行脚本，仅查 `debugger;` 与 localhost 硬编码 | `front/scripts/lint.mjs` | CI 的 lint 关卡形同虚设 |
| D20 | `tsconfig.json` `strict: false` | `front/tsconfig.json` | 类型守门削弱 |

---

## 7. 创新性改进建议（3 项）

> 选型原则：技术可行性优先（均基于现有技术栈，无需替换语言或框架）、能显著提升项目价值、且能同时缓解上文至少一个 P0/P1 问题。

### 建议一：CRDT 本地优先协同规划引擎（Local-First Collaborative Planner）

**问题锚点**：当前多人协同依赖 WebSocket 广播 + 服务端最后写入胜出，`PlanVariant` 投票与节点批注在并发编辑下会产生语义冲突；断网即完全不可用；D7 的 10.5s 延迟在弱网下体验进一步劣化。

**方案内核**：引入 Yjs（或 Automerge）作为前端协同状态层，将「行程节点序列 + 批注 + 投票」建模为 CRDT 文档，WebSocket 仅承载二进制增量更新（`y-websocket` 协议），服务端做更新日志持久化与快照。

**技术可行性论证**：
- 现有 `gateway/internal/service/hub.go` 的房间广播模型可保留，仅将 payload 从 JSON 改为 CRDT update（二进制）；
- 现成 `ws_handler.go` 已有帧大小上限（1 MiB，`pump.go:19`）与每房间协商互斥（`ws_handler.go:41-60`），天然契合同步协议；
- 投票语义可用 CRDT 的 `ORSet`（observed-remove set）精确表达「一人一票 + 后票覆盖前票」，与现有 `PlanVariantVote` 唯一索引语义等价但可离线；
- 离线队列 + 恢复后自动合并，解决弱网（尤其境外旅行场景）可用性。

**实施步骤**：
1. 第 1–2 周：前端引入 Yjs，先在 `NodeAnnotationPanel` 单点验证（批注是天然的 CRDT 用例），建立 `y-indexeddb` 本地持久化；
2. 第 3–4 周：Gateway 增加 `internal/service/crdt.go`，实现 update 中继 + 二进制快照落库（新增 `crdt_snapshots` 表，`room_id` 主键 + `state_vector` + `blob`）；对现有 `RoomMember` 做鉴权复用（`requireRoomMember`）；
3. 第 5–6 周：迁移 `PlanVariant` 投票至 `ORSet`；保留服务端 `PlanVariant` 表作为可查询投影（投影更新走现有 `database.DB`）；
4. 第 7–8 周：加入离线关卡（`navigator.onLine` + 队列可视化）、冲突可解释 UI（凸显「谁改了什么」）。

**预期效果**：
- 断网连续编辑可用，恢复后自动收敛，**消除「最后写入胜出」导致的数据丢失**；
- 协同消息体积由 JSON 全量转为增量二进制，按现有批注/投票/g 数据规模估算可**降低 60–80% 同步流量**；
- 为用户体验直接加分项（离线可规划），差异化明显。

**资源需求**：前端 1.5 人月 + 后端 1 人月；新增依赖 `yjs`、`y-indexeddb`（约 40 KB gzip）；无新增基础设施成本。

**风险与缓解**：Yjs 文档结构与现有 JSON 契约需双向映射 —— 采用「CRDT 为真相源、SQL 为读投影」的双写模式，投影可随时从快照重建，避免迁移期数据不一致。

---

### 建议二：LLM 成本与延迟治理网关（Cost-Aware Model Router）

**问题锚点**：B8（无并发上限、无 token 计量、无成本预算）+ S-4（未授权可烧额度）+ `agent.py:2059-2134` 约 1,000 行的巨型 prompt 且重复发送两次（`:2316` 与 `:2278`）+ `SemanticItineraryCache` 命中率无监控。

**方案内核**：在 AI Service 前增加轻量路由层（同进程模块即可，无需新服务），实现四件事：
1. **语义缓存前置**：把已验证存在的 `SemanticItineraryCache`（MD5 + embedding 余弦，阈值 0.92，TTL 86400）提升为**必经关卡**，并补充命中率指标与 prometheus 暴露；
2. **模型分级路由**：`qwen-turbo`（草稿/校验/分类）→ `qwen-plus`（主规划）→ 高价模型（仅在用户显式选择「深度规划」时），按请求复杂度（候选池规模、成员数、天数）自动选级；
3. **Token 预算与准入**：按用户/房间维度设置日/时 token 配额（复用 Gateway 已有 Redis 限流原语，`ratelimit.go:83-115`），超额降级到 `_synthesize_from_pool` 确定性路径（该路径已存在且带 `degraded_fallback` 标记）；
4. **Prompt 去重与压缩**：修复 `system_prompt` 重复发送，将候选池 hint 的 80 字符截断上限做成可配置，并把巨型 prompt 外置到已存在但为 0 字节的 `core/prompts.py`。

**技术可行性论证**：
- 语义缓存与降级路径**均已实现**，本建议主要是「接线 + 计量 + 分级」，不是从零开发；
- `core/prompts.py`（0 字节、被跟踪但零引用）正是为此重构预留的位置；
- 分级路由只需在 `agent.py:2321` 附近的 `model=MODEL_NAME` 处引入决策函数，改动局部；
- token 配额可复用 Gateway 侧 Redis 限流模式，避免引入新组件。

**实施步骤**：
1. 第 1 周：修复 prompt 重复发送（`:2278` 复用变量），补 `HEARTBEAT_INTERVAL_SEC`（`:44` 已声明但从未引用）或删除；
2. 第 2–3 周：将 prompt 全量迁移至 `core/prompts.py`，建立单一真相源；同步消灭 `conflict.py:13-17` 与 `agent.py:34-39` **两套 LLM 客户端默认值不一致**的问题（模型分别是 `qwen2.5:0.5b` 与 `qwen-turbo`）；
3. 第 4 周：缓存强制前置 + 命中率/节省额度指标；
4. 第 5–6 周：实现 `select_model(complexity)` 分级路由 + token 配额与降级；
5. 第 7 周：A/B 验证（复用现有 `control/treatment` 分流与漏斗埋点，`:132` 已有 `/recommendation/funnel`）。

**预期效果**：
- 语义缓存命中直接省去整次 LLM 调用；同城/同画像重复规划预计命中 20–40%（需 A/B 确认）；
- 分级路由将非关键调用（冲突检测、分类）成本下降约一个数量级（`qwen-turbo` vs `qwen-plus` 价差量级）；
- 从「额度耗尽才发现」变为「配额内可控降级」，消除未授权烧额度的财务风险面。

**资源需求**：后端 1 人月；无新增基础设施；需要 DashScope 各模型单价表用于成本核算（一次性调研 0.5 人日）。

---

### 建议三：概率化「数字孪生」仿真与风险调整行程（Monte-Carlo Digital Twin）

**问题锚点**：现有 `DigitalTwinHandler` 与 `/planning/crowd/predict` 输出的是确定性点估计（且 `estimate_crowdedness` 的 `percent` 甚至是与 score 无关的固定值）；`team_satisfaction` 为硬编码；D10「伪精确指标」是当前最伤害可信度的问题。

**方案内核**：把点估计升级为**分布**。对每个行程方案做 N=2,000 次蒙特卡洛仿真，采样维度包括：
- 天气（基于 `risk_service.py:297-314` 已有的 19 词关键词→分数表 + 气温正则，扩展为历史同月分布）；
- 排队/拥挤（用 `estimate_crowdedness` 的 score 替代固定 percent，并与真实 POI rating/review_count 关联）；
- 交通时延（Amap 实时路况 `status_code` 映射到对数正态分布）；
- 用户节奏（`time_intelligence.analyze_pace` 的 rush/balanced/deep 映射到每日可承受节点数与停留时长分布）。

输出改为 **P50/P90 时长、超预算概率、方案满意度分布**，并据此做**风险调整排序**（如「P90 不超预算」作为硬约束参与 `select_fair_route` 的 maximin 比较）。

**技术可行性论证**：
- 纯计算、无外部依赖，`optimization.py` / `constraints.py` 已被设计为纯函数且带测试（现有 `test_optimization.py` 的强断言可平滑扩展）；
- numpy 已在 venv 中（虽未声明依赖 —— 本建议同时要求补入 `requirements.txt` 并加锁）；
- 2,000 次向量化仿真对单方案耗时在 10–50ms 量级，可接受；且可缓存「天气分布」「拥堵分布」这些与方案无关的中间量；
- 输出结构可复用现有 `RiskReport` 的 `source`/`is_estimated` 溯源约定，保持数据纪律一致。

**实施步骤**：
1. 第 1 周：新增 `core/simulation.py`，实现分布采样器与向量化仿真核心；补齐 numpy 声明与 `requirements.txt` 版本锁定（顺带修复 D14）；
2. 第 2 周：把 `estimate_crowdedness` 的固定 `percent` 替换为基于 score 的真实映射（修复 D10 最小可用切片），并补单测断言「percent 随 score 单调」；
3. 第 3–4 周：仿真接入 `select_fair_route`（`optimization.py:216`），引入风险调整排序与「P90 不超预算」约束；
4. 第 5 周：前端在 `ReplanProposalPanel` / `PlanVariantBar` 展示 P50/P90 与超预算概率，替代裸点估计；
5. 第 6 周：把硬编码 `team_satisfaction` 替换为仿真输出的成员效用分布（复用 `member_utilities`，`optimization.py:181-217`）。

**预期效果**：
- 用户可见指标从「伪造的 96/94/93」「与 score 无关的 82%」变为**可解释的分布与置信区间**，直接消除 D10 的信任风险；
- 提供竞品普遍缺失的「P90 风险预算」决策依据，构成可传播的差异化卖点；
- 为 `PlanningEvent` 埋点体系提供更丰富的学习信号（分布特征可用于 bandit 的上下文向量，缓解 B6 中 bandit 探索仅 1 次的问题）。

**资源需求**：算法 1.5 人月 + 前端 0.5 人月；计算资源增量极小（CPU 密集，单实例可承载）；需 1 次历史天气/客流数据源调研（0.5 人月）。

---

## 8. 功能拓展方案

> 按「是否复用现有基础设施」排序，均给出模块、步骤、预期效果、资源需求。

### 8.1 模块一：凭证与账号安全中心（P0，直接封堵 S-1/S-3）

| 项 | 内容 |
|---|---|
| 新增能力 | 修改密码、忘记密码（邮件/短信令牌）、吊销全部会话、活跃设备列表、登录失败锁定 |
| 复用 | `sessionStore`/Redis 会话分支（`auth.go:98-162`）、`fixedWindowLimiter`（`ratelimit.go:21-77`）、`User` 模型 |
| 实施步骤 | ① 新增 `POST /api/auth/password`（校验旧密码 + bcrypt 重哈希 + **吊销该用户全部会话**）；② 新增 `GET/DELETE /api/auth/sessions`（Redis 侧维护 `omni:user_sessions:<uid>` 集合）；③ 登录/注册挂 `AuthRateLimitMiddleware`（每 IP + 每用户名双维度，5 次失败后指数退避，复用现有 `distributedAllow`）；④ 修复 `sessionRedis()` 的 `redisOnce` 永久缓存错误，改为失败后可重试 + 熔断半开；⑤ 修复 `user_id` 必填缺陷（`http_api.go:434`） |
| 预期效果 | 消除认证单点故障（Redis 抖动不再全站不可用）；暴破速率从 13 次/秒降至 5 次/分钟/账号（约 **1/156**）；用户获得自救能力 |
| 资源需求 | 后端 0.5 人月；无新增基础设施 |

### 8.2 模块二：行中实时协同与离线保障（P1）

| 项 | 内容 |
|---|---|
| 新增能力 | 离线编辑队列、断网自动续传、协同冲突可视化、位置共享与集合点、实时费用分摊结算 |
| 复用 | `hub.go` 房间广播、`collaborationPresence.ts`、`useOmniRouteWs.ts`、`BudgetPanel`/`ExpenseReportModal` |
| 实施步骤 | ① 接入 CRDT（见建议一）；② `TripExecutionState`（已存在，`db_models.go:162-173`）扩展为可离线写；③ 费用分摊：在 `ExpenseRecord` 增加 `paid_by` 与 `beneficiaries`，新增结算算法（最小转账次数，纯算法易测）；④ 位置共享走现有房间 WS，仅在用户显式开启时广播 |
| 预期效果 | 境外/弱网场景可用性从「完全不可用」变为「可用」；费用分摊消除旅行中最常见的线下扯皮痛点 |
| 资源需求 | 前端 1.5 人月 + 后端 1 人月 |

### 8.3 模块三：签证/入境合规与多模态票务闭环（P2）

| 项 | 内容 |
|---|---|
| 新增能力 | 签证要求查询与材料清单、入境政策时效提醒、票务（机票/高铁/门票）库存与价格直连、下单-改签-退票生命周期 |
| 复用 | **已存在的 `TravelOrder` 模型**（`db_models.go:277-289`，含 `IdempotencyKey` 唯一索引与 `CancellationNote`）与 `CreateOrderHandler`/`CancelOrderHandler`（`route.go:104-105`）；`.env` 已预留 `TUNIU_*` / `TRIPCOM_*` / `ROLLINGGO_*` 供应商配置位 |
| 实施步骤 | ① 先把订单生命周期跑通（当前已有创建/取消，补改签与状态机持久化）；② 接入 `.env.example` 已声明的 MCP 供应商（`TUNIU_FLIGHT_API_URL` 等），统一走 `mcp_provider.go` 已有的适配层；③ 签证合规模块：新增 `internal/service/visa.go` + 季节性数据表，接入规划流程作为硬约束（复用 `constraints.py` 的 `LEVEL_HARD`） |
| 预期效果 | 从「行程建议工具」升级为「可成交的行程履约平台」，商业模式闭环；签证硬约束可避免规划出无法成行的行程（当前是纯软提示） |
| 资源需求 | 后端 2 人月 + 前端 0.5 人月；需供应商商务对接与合规审查 1 人月；**外部依赖：MCP 供应商授权** |

### 8.4 模块四：可观测与运营中台（P2，同时解决 D16/B8）

| 项 | 内容 |
|---|---|
| 新增能力 | Prometheus 指标暴露、分位数延迟直方图、安全审计日志、LLM 成本看板、告警规则 |
| 复用 | 现有 `METRICS`（`observability.py:37-77`）与 Gateway `recordRequest`（`observability.go:49-58`） |
| 实施步骤 | ① 将累计计数改为**滑动窗口 + 直方图**（当前 `avg_latency_ms` 是进程生命周期均值，会持续钝化，失去告警价值）；② `/metrics` 迁至独立管理端口或加管理员令牌（修复 S-5）；③ 新增 `security_events` 表或结构化日志通道，覆盖登录成败、`USER_MISMATCH`、限流触发；④ 接入 Prometheus + Grafana，定义 SLO（如规划 P95 < 30s、风险 P95 < 3s） |
| 预期效果 | 从「靠日志人工目测」（现有 `gw_dev_stderr.log` 显示延迟靠肉眼看日志）升级为可告警、可回归的性能治理；安全事件可追溯 |
| 资源需求 | 后端 1 人月 + 运维 0.5 人月；Prometheus/Grafana 基础设施成本可忽略 |

---

## 9. 用户交互流程优化

| 优化点 | 现状问题（证据） | 优化方案 | 预期效果 |
|---|---|---|---|
| **错误反馈缺失** | 实测 `profile/update` 与 `register` 失败返回 **空 body**（400/409） | 统一错误契约 `{code, message, detail?, request_id}`；Gateway 侧保证每条 `c.JSON` 都带 `code`（现有部分已带，如 `AUTH_REQUIRED`，需补齐）；前端统一拦截器按 `code` 映射中文文案 | 用户从「点了没反应」变为「明确知道为什么失败、怎么改」 |
| **规划等待 10s+ 无进度** | `risk/realtime` 实测 10.5s；`agent/negotiate` 虽有 NDJSON 流式（`:3226`）与每 600 token 进度（`:2335`），但 `HEARTBEAT_INTERVAL_SEC=8`（`:44`）**声明后从未使用** | 实现真正的 NDJSON 心跳帧（利用已声明的常量）；补阶段化进度（候选获取 → 打分 → LLM → 校验 → 路线绑定）；前端 `DraftingPanel` 展示阶段而非仅分钟级 spinner | 长任务的感知等待大幅下降；10.5s 的风控从「卡死」变为「可见进展」 |
| **风险订阅无聚合视图** | `refresh_subscriptions` 对每个城市串行做完整 3 次调用（`operations.py:99-103`） | 改为 `asyncio.gather` 并发 + 结果级 TTL 缓存（同城 5 分钟内复用） | 多城市订阅刷新延迟从 O(n×10.5s) 降至约 O(10.5s) |
| **移动端信息密度过高** | `ContextualLobby.tsx` 4,760 行、含 25 个组件定义，承载全部功能；E2E 已在防横向溢出，说明移动端曾是痛点 | **先删除 §3.0 的死代码**，再按「行前/行中/行后」拆分为代码分割的路由段（Next 16 App Router 原生支持）；`next/dynamic` 懒加载地图与 3D 地球**已做对**，需对工作区各面板照做 | 首屏 JS 从 1,096KB 降至目标 <700KB（framer-motion 改 `LazyMotion`、`provinceData` 改懒加载）；弱网首屏可交互时间显著改善 |
| **无键盘可达性保障** | 未发现系统性的焦点管理与 ARIA 标注 | 对 `DraggablePanel`、`ExpandableConnectorNav`、`BottomMapCarousel` 补键盘导航与 `aria-*`；引入 `eslint-plugin-jsx-a11y`（当前 lint 仅 38 行，见 D19） | 可访问性达标，同时用真正的 ESLint 替换形同虚设的自制 lint |
| **表单校验前置不足** | `UpdateProfileHandler` 在服务端才拒绝「无可更新字段」（`:468-471`） | 前端按 `code` 做即时校验与按钮禁用；`user_id` 由统一 API 客户端自动注入 | 减少无效往返 |

---

## 10. 性能瓶颈解决策略

### 10.1 P0 级（立即，1–2 周内）

| 瓶颈 | 措施 | 预期改善 | 证据锚点 |
|---|---|---|---|
| B1 风控串行 3 调用 | `operations.py:61-74` 改 `asyncio.gather` 并发；同城加 5 分钟结果缓存 | 10,563ms → **约 8,400ms**（受最慢的 safety_intel 支配）；命中缓存时 → **<50ms** | 实测组件耗时 1782/998/8355ms |
| B7 Redis 重连日志洪泛 + S-1 | 修复 `sessionRedis()` 的 `redisOnce` 错误缓存；`StartEventStreamBridge` 加指数退避 + 仅在 Redis 已配置时启动（当前 `eventstream.go:31-38` 已有空值短路，但配置了却不可用时会无限刷） | 消除每 3.7s 一轮的日志与拨号开销；认证恢复可用 | 实测 stderr 洪泛 |
| B2 社区 N+1 | `http_api.go:527-535,547` 改为单次 `GROUP BY` 聚合 + 批量 `WHERE id IN ?`（`variant.go:270` 已有可复制范式）；排序下推 SQL | 最坏 ~2,000 次查询 → **3 次** | `Limit(500)` 与逐帖计数 |
| B3 标签全表扫描 | `http_api.go:787-788` 改为 SQL 侧聚合或游标分页 | 单请求由 O(全表) 降为 O(结果集) | 无 limit 的 `Find` |
| B4 连接池无上限 | `db.go` 增 `sqlDB.SetMaxOpenConns(1)`（写路径）+ 独立读池 + `SetConnMaxLifetime`；`logger.Silent` 在非生产改 `Warn` | 从「连接抖动 + SQLITE_BUSY」变为可控排队；慢查询可发现 | 全库无 `SetMaxOpenConns` |

### 10.2 P1 级（1–2 月）

| 瓶颈 | 措施 | 预期改善 |
|---|---|---|
| B5 前端巨型组件与死代码 | **零风险第一步：删除 §3.0 列出的 18 个死文件（3,289 行）与 `ContextualLobby` 内 3 处死定义（~949 行）**；第二步按业务域拆分 `ContextualLobby`（目标 <800 行/文件）；`next/dynamic` 懒加载 `WorldSafetyGlobe`（66KB+three.js）已做对，继续对工作区各面板照做；把 75 个 useState 收敛为 `useReducer` + Context 分片 | 死代码清零（-22.1%）；首屏 JS 1,096KB → **目标 <700KB**（framer-motion 改 `LazyMotion`/`m.`、`provinceData` 改懒加载可再省约 160KB）；重渲染范围从整页收敛到子树 |
| B6 进程内状态无界 | `_api_cache` 加 LRU 上限；`SemanticItineraryCache._vec_index` 加 maxsize + TTL 清扫；bandit `_arms` 加容量上限与淘汰；bandit 状态改存 SQLite/Redis 以支持多副本 | 内存占用从「随请求数单调增长」变为有界；多副本学习状态一致 |
| B8 LLM 无治理 | 见建议二 | 成本可控 + 未授权烧额度风险消除 |
| B9 依赖漂移 | Python 补 `numpy/networkx/sklearn/redis` 声明并引 `pip-compile` 锁文件；前端 5 个 `latest` 改为精确 caret；CI `go-version-file: gateway/go.mod` 对齐 | 全新环境可一次装成；CI pin 恢复意义 |
| 社区查询 | 补 `Comment(PostID)`、`PostFavorite(PostID)`、`Message(RoomID,CreatedAt)` 复合索引 | 列表与聊天历史查询走索引 |

### 10.3 P2 级（季度级，架构演进）

| 项 | 措施 | 触发条件（何时必须做） |
|---|---|---|
| **SQLite → PostgreSQL** | GORM 已抽象，主要改 `db.go`（PRAGMA → 连接串）、`VACUUM INTO` 备份改为 `pg_dump`、`AutoMigrate` 改为正式迁移工具 | 并发写者 >1 成为瓶颈，或房间协同上线后写 QPS 持续 >50 |
| **无状态化 Gateway** | 会话/限流已支持 Redis，剩余 `planning_bandit` 与 `_api_cache` 外移；`gateway.pid` 手工 PID 替换为容器编排 | 需要多副本或滚动发布 |
| **水平扩展 AI Service** | 语义缓存外置 Redis（已具备 fallback 路径）、bandit 状态集中、gRPC/HTTP 内网通信 | 单实例 LLM 并发打满 |
| **数据保留与归档** | 为 `messages`/`planning_events`/`recommendation_events` 加 TTL 与分区归档任务 | 表行数 >1,000 万或磁盘增长告警 |
| **异步任务队列** | 把 10.5s 级的风控刷新、行程复盘生成、数据集导出改为后台任务 + 回调/轮询 | 同步接口 P95 超过 SLA |

---

## 11. 实施路线图

### 阶段一：止血（第 1–2 周）— 目标：可安全演示

| 序 | 任务 | 对应问题 | 工作量 |
|---|---|---|---|
| 1 | 修复 `sessionRedis()` 错误缓存，认证在 Redis 缺失时走内存回退或明确快速失败 | S-1, D1 | 0.5 天 |
| 2 | `git rm --cached` 四个构建/运行产物；`git filter-repo` 清理历史；**轮换全部 4 个真实密钥** | S-2, D2, D6 | 1 天 |
| 3 | 登录/注册挂限流 + 失败锁定；统一 401 语义消除用户枚举 | S-3, S-5(枚举) | 1 天 |
| 4 | AI Service：内部认证默认开启并 fail-closed；绑定 `127.0.0.1`；`reload=False` | S-4 | 0.5 天 |
| 5 | `/metrics` 迁移至管理端口或加管理员令牌 | S-5 | 0.5 天 |
| 6 | `isPlanningAdmin` 拒绝空环境变量；模型端点加内网地址 denylist | S-7, S-8 | 0.5 天 |
| 7 | **立即执行 `git add` 全量源码提交**（当前 72.3% 无版本保护） | D5 | 0.5 天 |
| 8 | 修复 `user_id` 必填导致的资料更新不可用；统一错误码语义（校验失败不再返回 200） | D17, D18 | 0.5 天 |
| 9 | **删除前端全部死代码**（18 个零导入文件 + `ContextualLobby` 内 3 处死定义），并补 `app/error.tsx` 错误边界 | D0, D9 | 0.5 天 |
| 10 | Gateway 显式 `http.Server` + 读写超时 + 优雅停机 | D13 | 1 天 |

**阶段交付标准**：Redis 下线不影响登录；暴破被限流；无真实凭证在仓库；源码 100% 入库；资料更新可用；前端死代码清零且有错误边界。

### 阶段二：性能与质量（第 3–8 周）— 目标：可对外试用

| 序 | 任务 | 对应问题 | 工作量 |
|---|---|---|---|
| 1 | 风控并发化 + 结果缓存；风险订阅批量并发 | B1, D7 | 1 周 |
| 2 | 社区 N+1 与全表扫描修复 + 补索引 + 连接池上限 | B2, B3, B4, D8 | 1.5 周 |
| 3 | 事务化与唯一索引补齐（username、risk_subscription、写路径事务） | D15, S-2(账号混淆) | 1 周 |
| 4 | 前端拆分与懒加载（首屏 JS 目标 <600KB） | B5, D9 | 2 周 |
| 5 | 依赖锁定（Python 补声明 + lock；前端去 `latest`；CI 工具链对齐） | B9, D14 | 0.5 周 |
| 6 | 真 ESLint + `strict: true`（分阶段开启）替换 38 行自制 lint | D19, D20 | 0.5 周 |
| 7 | Prometheus 指标 + 分位数 + 安全审计日志 | D16, §8.4 | 1 周 |
| 8 | 为 `run_negotiate` 与 `risk_service` 补单测（当前 3,040 行零测试） | D12 | 1.5 周 |

**阶段交付标准**：核心接口 P95 达标（风控 <3s 命中缓存、规划 <30s）；首屏 JS <600KB；核心算法测试覆盖 >60%。

### 阶段三：差异化（第 9–20 周）— 目标：形成竞争壁垒

| 序 | 任务 | 对应建议 | 工作量 |
|---|---|---|---|
| 1 | LLM 成本与延迟治理网关 | 建议二 | 1 人月 |
| 2 | 概率化数字孪生仿真；替换伪造指标 | 建议三 | 2 人月 |
| 3 | CRDT 本地优先协同 | 建议一 | 2.5 人月 |
| 4 | 凭证安全中心 + 费用分摊结算 | §8.1, §8.2 | 1 人月 |
| 5 | 订单履约闭环 + 签证合规 | §8.3 | 2.5 人月 + 商务对接 |
| 6 | SQLite → PostgreSQL（视负载触发） | §10.3 | 2 人月 |

### 11.1 总资源需求汇总

| 类别 | 阶段一 | 阶段二 | 阶段三 | 合计 |
|---|---|---|---|---|
| 后端（人月） | 0.6 | 4.5 | 6.0 | **11.1** |
| 前端（人月） | 0.2 | 2.0 | 2.0 | **4.2** |
| 算法/数据（人月） | 0 | 1.5 | 2.5 | **4.0** |
| 运维/SRE（人月） | 0.1 | 0.5 | 1.0 | **1.6** |
| 商务/合规（人月） | 0 | 0 | 1.0 | **1.0** |
| **合计** | **0.9** | **8.5** | **12.5** | **≈22.4 人月** |

**基础设施成本**：阶段一/二无需新增（Prometheus+Grafana 可单机部署）；阶段三需评估 PostgreSQL 托管实例与 LLM 分级调用的净成本变化（预计因缓存与分级路由，**LLM 支出不升反降**）。

**外部依赖风险**：MCP 票务供应商授权（阶段三模块五）为唯一不可内部消化的依赖，建议提前启动商务流程。

---

## 12. 结论与优先级建议

### 12.1 总体判断

OmniRoute 的问题不是「功能不足」，而是**工程交付成熟度落后于功能野心约一个阶段**。它已经具备一个可演示、可讲解、测试真实通过的完整产品原型，算法层有真材实料（Pareto/maximin/DBSCAN+TSP/五维风险复合），且数据溯源纪律与降级链设计明显超出同阶段项目水准。阻碍它走向生产的是**配置加固、并发架构与工程卫生**这三类「非功能性」问题，而非缺少功能。

### 12.2 如果只能做三件事

1. **修复 Redis 导致的认证单点故障**（S-1）——这是唯一一个「一个可选依赖的抖动就让全站无法登录」的缺陷，1 行逻辑修复换取可用性。
2. **清理 Git 中的凭证与二进制并全量提交源码**（S-2, D5）——同时消除隐私泄露与「72.3% 代码无版本保护」的资产风险，是成本最低、收益最高的动作。
3. **给登录接口加限流**（S-3）——现有 `fixedWindowLimiter` 与 `distributedAllow` 已完全可复用，不到 1 天的改动把暴破速率降低两个数量级。

### 12.3 唯一需要警惕的技术叙事风险

代码中 `NashEquilibriumSolver`（无纳什计算）、`ContextualThompsonBandit`（无 Thompson 采样）、时间智能（关键词查表）以及硬编码的 `team_satisfaction` 96/94/93 与固定 `percent` 82/58/35，构成了一个**「命名与实现脱节」**的模式。这类问题在演示中不易被发现，但一旦被技术评审或用户识破，会同时损害产品可信度与技术声誉。建议在阶段二内统一处理：能改为真算法的改（Thompson 采样只需引入 Beta 分布采样，改动很小），无法实现的**如实改名**，所有估算值一律打上已有的 `is_estimated` 标记——项目在其他地方已经建立了极好的溯源纪律，这里只需要保持一致性。

### 12.4 优势的可放大之处

项目最强的两项能力——**数据溯源纪律**（A3）与**多层降级链**（A4）——恰好是当前旅行 AI 产品最稀缺的信任基础设施。建议三（概率化仿真）正是把这两项优势从「防御性设计」转化为「进攻性产品力」的最短路径：它不需要新架构，只需要把已有的确定性启发式升级为可解释的分布，就能把「我们不会瞎编数据」变成用户可感知的差异化卖点。

---

*报告完 · 所有量化数据均来自本次实测，未经验证之处已在正文标注*
