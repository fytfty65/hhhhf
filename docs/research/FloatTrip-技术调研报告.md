# FloatTrip（途见）技术调研报告

> 调研对象：`https://github.com/shouzhuoshouzhuo/FloatTrip`
> 调研方式：仓库以 `git clone` 全量拉到本地（`main` @ `ec911f7`，已 `git fetch --unshallow`，56 次提交），逐文件阅读代码与文档；GitHub 元数据经第三方 GitHub 镜像 API 读取（本机直连 `api.github.com` 被网络策略阻断，`raw.githubusercontent.com` / `git` 协议可用）。
> 面向读者：OmniRoute（Go 网关 + Python 多智能体 `ai-service` + Next.js 前端的多智能体旅行规划助手）作者。

---

## 一句话定位

**途见 / FloatTrip 是一个真实存在、可运行的 Python + LangGraph 多 Agent 旅行规划 Web/App 项目**：对话澄清 → 结构化 Planning Brief → 高德真实 POI + 天气 → `Planner ⇄ Reviewer` 循环 → `Time Check` 专项修正 → 餐饮/贴士 → 可编辑地图行程；代码规模约 39 个 Python 源文件 + 一个无构建的 JSX SPA + 一个 Bare React Native App，**有真实的离线单测与一套独立的 Agent 评测框架（pass@k / pass^k / 评审误判率）**。

| 项目 | 事实 |
|---|---|
| 仓库 | `shouzhuoshouzhuo/FloatTrip`，默认分支 `main` |
| 创建时间 | 2026-05-21（第三方镜像 API `createdAt`） |
| 最近推送 | **2026-08-13**（`pushedAt`）；本次 clone 的 `main` 顶端提交 `ec911f7` = `docs: add FloatTrip project gallery`，提交时间 2026-08-13 13:31 +0800 |
| 调研当日 | 2026-09-18 → **最近一次代码推送约 5 周前** |
| Star / Fork / Watcher | **77 / 14 / 2** |
| 提交数 | 56（`git rev-list --count main`） |
| 贡献者 | `chj <2965908658@qq.com>` 55 次、`haojie cheng` 1 次 → **实质单人项目** |
| 分支 | `main`、`feature/manual-edit`（`b6d0097`，已合入 main，残留分支） |
| 形态 | **Web 单页 SPA（FastAPI 同端口托管）+ Bare React Native App（iOS/Android）+ 一个纯视觉原型 `mobile-prototype/`**；不是 CLI |
| 语言/框架 | Python 3.10+（FastAPI + Uvicorn + LangGraph ≥1.2 + langchain-openai + Pydantic v2 + Redis 可选）、前端无构建 JSX、移动端 RN 0.86 + TS + Fabric |
| LLM | DeepSeek / 豆包（`LLM_PROVIDER` 切换，走 LangChain OpenAI 兼容层） |
| 地图数据 | 高德：Web 服务 REST（POI/天气/步行路线）+ JS API 2.0（前端绘图）+ 原生 SDK（App） |
| License | ⚠️ **README 徽章写 `License: MIT` 并链接 `LICENSE`，但仓库根目录没有 `LICENSE` 文件**（`Get-ChildItem -Filter LICENSE*` 无结果；`raw.githubusercontent.com/.../main/LICENSE` 返回 **HTTP 404**）。GitHub 镜像 API 也未返回 license 字段 → **法律上目前是「保留所有权利」，不是 MIT** |
| CI | 只有 `.github/workflows/mobile-ci.yml`（`git ls-files .github` 确认已跟踪，2895 字节；覆盖 JS typecheck/lint/test/bundle:check、OpenAPI 类型漂移校验、Android Debug 构建、iOS Simulator 构建）；**没有 Python 服务端 CI、没有 eval CI** |

---

## 技术栈与目录结构摘要

```
FloatTrip/
├── run.py                     # 启动入口（uvicorn，默认 8765）
├── requirements.txt           # fastapi, uvicorn[standard], pydantic>=2, langgraph>=1.2,
│                              # langgraph-checkpoint-sqlite>=3.0, langchain-openai, httpx,
│                              # PyJWT>=2.0, redis>=5.0   ← 没有 pytest！
├── app/
│   ├── main.py                # FastAPI app；include 6 个 router；同时 mount frontend/ 静态目录
│   ├── api/                   # 纯 HTTP/SSE 层（transport）
│   │   ├── runtime_routes.py  # 对话 / Brief / Run / SSE（20KB）
│   │   ├── plan_routes.py     # optimize_day / revert_day / confirm_modification /
│   │   │                      # poi/search / PUT timeline / poi/nearby / route/walking
│   │   ├── history_routes.py, profile_routes.py, auth_routes.py, sweep_routes.py
│   ├── core/                  # 无 LLM 的基础层
│   │   ├── planning_constraints.py  # ★ 约束规范化 + "约束覆盖度" 投影（纯函数）
│   │   ├── travel_memory.py, memory.py, database.py（SQLite/WAL）
│   │   ├── cache.py（Redis 可选，缺失静默透传）, http.py, auth.py(JWT), env.py
│   ├── llm/                   # factory.py + deepseek.py + doubao.py
│   ├── providers/
│   │   ├── amap/poi.py        # 高德 POI 搜索/解析
│   │   └── weather/amap.py    # 高德天气预报
│   ├── chat/                  # 对话 Agent、记忆提取、PlanningBrief 服务、executor
│   ├── planning/              # ★ 核心
│   │   ├── schemas.py         # 所有 Pydantic 模型 + LangGraph State
│   │   ├── nodes.py           # 42KB，全部节点函数
│   │   ├── graph.py           # 4 张图（主图/修改迷你图/运行时修改图/确认图）
│   │   ├── helpers.py         # 纯函数：haversine、k-means 聚类、开放时间校验、餐厅解析…
│   │   └── prompts.py         # 全部 system prompt（仅 137 行，集中管理）
│   └── runtime/               # 持久化 Run：scheduler / worker / repositories /
│                              # stream(SSE) / observability / compat（32KB repositories.py）
├── frontend/                  # 无构建 JSX：index.html + main.jsx + components.jsx(27KB)
│                              # + pages.jsx(116KB!) + edit.jsx + api.js + chat-state.js
├── mobile-app/                # RN 0.86 + TS + Fabric；QZAMapView 原生组件；
│                              # .maestro/ E2E；src/api/schema.ts(77KB, OpenAPI 生成)
├── mobile-prototype/          # 纯视觉/交互原型（Vite + Playwright），非生产
├── tests/
│   ├── 16 个 test_*.py        # 离线单测（含 test_architecture_boundaries.py 架构边界测试）
│   ├── EVAL_GUIDE.md          # 评测手册（240 行）
│   ├── eval/                  # ★ Planner/Reviewer 评测框架
│   │   ├── harness.py, run_eval.py, report.py, capture_pool.py, generate_fixtures.py
│   │   └── graders/{code_graders.py, llm_judge.py, reviewer_reliability.py}
│   └── eval_query_rewrite/    # query_rewrite 节点专项评测（5 个 fixture，含"不发明"用例）
├── openspec/                  # 12 个 spec 能力文档 + 归档的 change（proposal/design/tasks）
├── docs/                      # project-introduction / agent-runtime / deferred-runtime-work …
└── xiaohongshu-floattrip/     # 小红书宣传图素材生成
```

**注意：仓库没有 `pyproject.toml` / `setup.cfg` / `pytest.ini` / `conftest.py`**，`requirements.txt` 里也没有 `pytest`——单测靠 `python -m unittest` 风格运行，未声明测试依赖。

---

## 值得借鉴的做法

### 1. 候选池「封闭世界」+ 确定性兜底校验，而不是靠提示词祈祷

**它的做法**：`attraction_search` 节点先用高德按 `{城市}必去景点`/`{城市}热门景区`/`{城市}博物馆` 三个关键词抓 POI，`filter_by_rating` 只保留 `rating ≥ 4.5` 的（`nodes.py:199-206`、`helpers.py:196-202`）。Planner 的 system prompt 明令「只从候选景点池挑景点」「景点 name 必须逐字复制候选池中的写法，不得新增、删减或替换任何文字」（`prompts.py:14-30`）。**关键在于它没有相信这句话**：`helpers.py:291-299` 的 `unknown_spots()` 用集合差集找出越界景点名，被评为 **G1 硬性失败**（`code_graders.py:35-37`），Reviewer prompt 里也把「非候选池景点」当作已由 Python 预计算好的客观事实喂进去（`prompts.py:34-36`）。

**为什么好**：LLM 写景点名幻觉是最难靠 prompt 根治的一类错误；把它降级成一次集合运算，就变成 100% 可复现、零 LLM 成本的门禁。更进一步，这条校验**同时被生产流程和评测框架复用**（`code_graders.py:17-22` 直接 `from app.planning.helpers import unknown_spots`）——生产和评测同一套真值，不会出现"评测过了但线上不一样"。

**落到 OmniRoute**：我们已有「行程骨架校验」的纯函数模块，建议把**「每个节点名必须命中当次候选池 ID 集合」**提升为硬门禁的第一条，并且在 `core/` 里做成 `unknown_nodes(itinerary, candidate_pool) -> list[Violation]` 这样单一签名，让规划质量门禁、二次增量解析、7 天分段修补三处**共用同一个函数**。评测集也直接 import 它，避免门禁口径漂移。（我们比它更进一步的地方：它只校验「名字在不在池里」，我们还可以校验「这个名字对应的 POI id 是否是本次搜索返回的那一条」，防止同名不同店。）

### 2. 代码评审（确定性）+ LLM 评审分层，把客观事实"算好再喂给模型"

**它的做法**：README「设计亮点 2」明确写「Reviewer 的判断依据由 Python 预先计算（每天地理跨度、开放时间冲突检测），以客观事实形式喂给 LLM」。代码侧对应 `helpers.py:268-288` 的 `open_time_violations()`、`helpers.py:72-129` 的 `cluster_pois_by_location()`（真实经纬度 k-means，固定种子保证可复现），以及 Reviewer prompt 里「基于候选池、**系统给出的『非候选池景点』等客观事实**」这句（`prompts.py:35`）。`tests/eval/graders/code_graders.py` 把确定性检查固化成 G1–G8。

**为什么好**：把"需要算术/需要集合运算"的判断从 LLM 手里拿走，既消除幻觉，又让同一份检查能用于打分。而且它给 k-means 用了**确定性初始化**（按经度排序等距取种子，`helpers.py:103-109`），使同一输入每次分区一致——这对回归测试和缓存是刚需。

**落到 OmniRoute**：我们的「规划质量门禁（硬约束 + 0–100 分）」应该拆成两层：**硬约束层全部是纯函数**（天数/住宿夜数/餐饮下限/节点是否在候选池/时间是否重叠/是否超营业时间），**打分层的 LLM 部分只做主观维度**（偏好贴合、节奏、主题连贯）。同时把我们 `core/` 里凡是涉及聚类/排序/分段的函数都补上**确定性种子**，否则评测 flaky。

### 3. 把「开放时间核查」拆成独立 Agent + 单向门，并把「只报确定的」写进 prompt

**它的做法**：主循环 `Planner ⇄ Reviewer` 收敛后，新增独立的 `time_check` 节点（`graph.py:85, 119-122`；`nodes.py:471-563`）。它有 3 条我认为很值得抄的工程细节：
- **职责单一**：Reviewer 不再负责开放时间（`prompts.py:34-49` 里根本没有开放时间条目），避免两个角色对同一类问题反复干预导致震荡（README 亮点 8 明说了这个动机）。
- **宁可漏报不可误报**：prompt 里明确「不确定是否违规时，不列入（宁可漏报，不可误报）」，并逐条定义四种违规情形、特别澄清「提前离场永远不是违规」「停止入园只约束 start_time」（`prompts.py:59-68`）。
- **单向门状态机**：`time_check_done` 一旦置 `True`，Planner 永远不再回 Reviewer（`nodes.py:442-452`），避免 A-B-A-B 振荡。

**为什么好**：这是"用图结构（拓扑）而不是用 prompt 措辞"来保证终止性与阶段性的教科书做法。把易错但可枚举的事实校验单独隔离，既让主循环更聚焦，也让失败影响面可控（`nodes.py:507-515`：`time_check` LLM 连续失败 → 静默跳过，不阻塞出结果）。

**落到 OmniRoute**：我们的「行程骨架校验」目前是纯函数、**在生成后一次性判定**。建议借鉴两点：(a) 把**开放时间/营业时间**单独拆成一个"事实核查与定向修补"阶段，和"偏好协商"阶段在图上分开；(b) 在状态里放一个**单向门布尔量**（如 `fact_check_phase`），一旦进入就不再回到协商阶段。这对我们「≥14 天按 7 天分段生成与修补」尤其重要——分段修补天然容易和总段协商来回震荡。

### 4. 「说改但没改」的机械检测 + 递增强度的提示注入（staleness detection）

**它的做法**：Planner 每轮返回后，把新旧 route 都 `json.dumps(..., sort_keys=True)` 做**字符串全等比较**（`nodes.py:320-332`）。如果上一轮明明有 `route_modify_opinion`（即被打了回）却 route 一字未变，就写入 `route_stale_warning`；下一轮 Planner 的 human message 最前面会注入一段 🚨 级别的严厉警告（`nodes.py:247-256`），明说「这说明你只改了 reasoning/notes 文字，但 days 里的景点列表原封不动地回显了旧版本……如果 days 再次与上一版完全相同，将被系统标记为规划失败」。同时它还有 `spot diff`（`nodes.py:306-318`）把「新增：X｜移除：Y」记进对话历史，让"改了什么"对人和模型都可见。

**为什么好**：CoT/`notes` 字段让模型很容易"表演修改"——文字上认错、结构上不动。它用一次 JSON 比较就把这种表演变成机器可判定的信号，而且**惩罚是递进式**的（先记录，再在最显眼位置强警告，下一轮失败可被评测捕获）。这是一种通用且廉价的"反空转"机制。

**落到 OmniRoute**：我们的二次增量解析（"不想去 X"、"多吃点地道美食"、"升级住宿"）最容易踩这个坑——LLM 回复"已为您调整"但结构化结果没变。建议在 `ai-service` 的增量解析路径上加一个 `diff_skeleton(old, new) -> ChangeSet`，若 `ChangeSet` 为空而用户明确提了修改意图，则**不进入门禁打分，直接判定为解析失败并重试一次**（带"上次未产生任何字段变化"的显式提示）。这比再多写十行 prompt 有效。

### 5. 约束的「极性 / 来源 / 覆盖度」三元组——把"这条偏好到底落地到哪一步"变成数据

**它的做法**：`core/planning_constraints.py` 是一份完全无 LLM 的纯模块。每条约束被规范化为 `{id, category, value_text, polarity, source, evidence_sequences}`（`planning_constraints.py:29-54`），`polarity` 有 `prefer/avoid/require/fact`，渲染进 prompt 时翻译成人话前缀：「优先考虑／必须避开／必须满足／仅作背景」（`constraint_directive()`，`planning_constraints.py:233-245`）。更有意思的是 `_coverage()`（`:109-130`）：每条约束声明它**应该被哪些阶段消费**（如 `attraction_preference → [attraction_search, planner, reviewer]`、`food_preference → [meal_search, meal_recommend]`、`accommodation_preference → [finalize]`），并给出 `status ∈ {applied, advisory, unverified}`，其中 `dietary_requirement` / `accessibility_need` 被显式标为 **`unverified`**，住宿和目的地历史标为 **`advisory`**。最终 `finalize` 把 `effective_constraints` 和 `constraint_coverage` 一起写进行程对象（`nodes.py:863-864`）。

**为什么好**：这是一套**"约束可追踪性"数据模型**——不是把偏好拼成一段字符串塞进 prompt，而是每条约束有 ID、有来源（`conversation` / `manual` / `long_term_memory`）、有作用级别（`preference` / `hard` / `context_only`）、有"我到底有没有被执行"的状态。它天然支持我们的诚信口径：**没有把握落实的约束，宁可标 `unverified` 也不假装 `applied`**。另外 `matching_fingerprint()`（`:95-106`）把目的地+预算+约束+记忆版本一起哈希，用来判断"同一份需求是否可复用上次规划"——这也是幂等/缓存的好抓手。

**落到 OmniRoute**：强烈建议在 `ai-service/core/` 增加同构模块，把「多角色偏好 → 折中」的结果从**一段自由文本**升级为**带 ID 的约束对象列表**。具体：`Constraint(id, role, polarity, target_stage, status)`，`status ∈ {applied, partially_applied, unverified, advisory}`；规划质量门禁按 `status` 计分（`unverified` 既不算满足也不算违反，但要在核对面板显示）。这样我们前端「核对面板」说的"哪些价格已核实、做了哪些替换、哪段还没补上"就有了**后端真值字段**可直接渲染，而不是前端再猜。

### 6. 三级降级策略被反复用在每一个外部依赖上（weather / Redis / LLM / 单天餐饮 / 地图）

**它的做法**，逐条都有代码：

| 依赖 | 降级行为 | 位置 |
|---|---|---|
| 高德天气 | 拿不到 → `weather_note = "天气信息获取失败，按晴天规划路线"`；超出约 4 天预报范围 → `"旅游日期超出天气预报范围…建议出行前关注天气预报"`；部分日期缺失 → 只对缺失日期提示 | `helpers.py:420-467` |
| Redis 缓存 | `REDIS_URL` 未配或 Redis 挂了 → `cache.py` 静默透传，无任何副作用 | README 亮点 10 |
| LLM 结构化输出 | `ainvoke_structured()` 对 `function calling` 偶发返回 `None` **重试 3 次**，全失败才抛明确 `RuntimeError`（不是 `AttributeError`） | `helpers.py:385-413` |
| 单天餐饮推荐 | 改为**按天独立调用 + 线程/协程并行**，单天失败自动降级取评分最高餐厅，并写明「（系统自动选取评分最高餐厅）」 | `nodes.py:624-649` |
| `time_check` | LLM 连续失败 → 跳过核查并写进 history，不阻塞出结果 | `nodes.py:507-515` |
| `spot_tips` | 完全非关键路径，失败就无贴士 | `nodes.py:752-757` |
| 前端地图 | 未配高德 JS Key → `initAmapForDay` 返回 false，**降级为 SVG 相对位置示意图**（`<polyline>` + "地点示意 · 相对位置"文案） | `components.jsx:142, 174-228` |
| 记忆写入 | `finalize` 里写记忆包 `try/except: pass` | `nodes.py:778-782` |

**为什么好**：每一条降级都是**"降级后仍然给出可用产物 + 明确告知降级原因"**，而不是抛错或静默给假数据。特别是天气两条不同的 note 文案（"获取失败"vs"超出预报范围"）区分得非常干净——这正是"不确定性要暴露给用户"的正确粒度。

**落到 OmniRoute**：我们已经在长途出行方式上用「没有可核实来源就标未核实」。建议把这份降级矩阵**显式化**：为 `ai-service` 的每个外部依赖（POI 数据源、票价源、地图、天气）定义 `(正常值, 降级值, 降级原因文案)` 三元组，并把降级原因作为一级字段进最终 payload，前端核对面板直接列"本次有 3 项数据降级"。这比散落在各处的 `try/except` 更容易做回归测试。

### 7. 一套真的在跑 Agent 的评测框架：pass@k / pass^k / 误放行率 / 反驳率

**它的做法**（`tests/EVAL_GUIDE.md` + `tests/eval/`）：
- **输入冻结**：fixture 里写死 `pois` + `weather_forecast`，跳过 intent 和高德搜索，让"同一份输入"可复现且**不花 API 钱**（`EVAL_GUIDE.md:81-117`）。
- **确定性代码打分器**：`code_graders.py` 里 G1 封闭池 / G2 time_check 干净 / G4 结构合法 / G5 覆盖 / G6 天气合规 → `objective_pass`；G7 收敛 / G8 time_check 效率 单独看。**注意 G2 的语义**：它不重算开放时间，而是检查 `state.time_violations` 是否为空——即"LLM 核查器有没有留下未修的问题"。
- **LLM 评委**只打 5 个主观维度（偏好贴合 / 节奏契合 / 主题连贯 / 动线合理 / 天气适配），1–5 分，`temperature=0`，且 prompt 要求「不臆测未提供的信息」（`llm_judge.py:20-33`）。
- **Reviewer 可靠性**：用代码打分的客观真值当"标准答案"，统计 **误放行率**（reviewer 通过但客观未过——最危险）和 **误打回率**（`reviewer_reliability.py:25-39`）。
- **Planner 反驳率**：`_pair_rounds()` 用正则 `^\[第(\d+)轮\]\s*(Planner|Reviewer)` 从 `planner_reviewer_dialogue` 里配出「Reviewer 打回 → 下一轮 Planner 回应」的转移，再用 LLM 分类 `adopt / rebut / ignore`，并明确注释「反驳是当前 prompt 未设计的**涌现行为**」（`reviewer_reliability.py:1-7, 64-128`）。概念上直接对齐 Anthropic 的《Demystifying Evals for AI Agents》。
- **query_rewrite 专项评测**（`tests/eval_query_rewrite/`）三个指标里有一个叫 **`g_no_invention`**：「query 和画像均无偏好时，三字段应为 null，**不凭空编造**」。
- **只读的评测结果预览 API**：`GET /api/sweep/list` 和 `/api/sweep/trial`（`sweep_routes.py`）把真实跑批的 JSONL（含 transcript + final_plan + 各 grader 结果）做成可浏览接口，还带路径穿越防护。
- **评测结果有可视化界面**：`SweepPreviewPage`（`frontend/pages.jsx:2182`）提供文件/trial 下拉（✅/❌/💥 标记 pass/crash），并用**生产同一套 `adaptPlan()` + 行程详情组件**渲染该 trial 的 `final_plan`——即"把跑批结果当真实行程看"。

**为什么好**：大多数 Agent 项目止步于"我写了个评测脚本"。它做对了三件更难的事：(a) **把 LLM 评委的偏见用确定性 grader 校准**（误放行/误打回）；(b) **评测多轮协商行为本身**（反驳/忽略），而不只是评测最终产物；(c) **把"不发明数据"写成一个可量化的断言**（`g_no_invention`）。

**落到 OmniRoute**：这是本项目最值得整体移植的部分。具体建议：
1. 在 `ai-service` 建一个 `tests/eval/`，fixture = 冻结的候选池 + 冻结的"价格来源快照"（含若干**故意缺失**的票价），跳过真实供应商调用。
2. 硬约束 grader 直接 import `core/` 的校验纯函数（我们已有），保证口径唯一。
3. 专门加一个**诚信 grader**：任何输出价格字段若 `tier == verified` 就必须能在 fixture 的供应商快照里找到对应记录，否则判失败；`tier == unverified` 但值为 `0` 也判失败。这是把我们的硬规矩变成 CI 里的一条断言——**比写在文档里强一百倍**。
4. 加一个**二次增量解析的回归集**：每条 fixture 形如「原行程 + 一句修改意图 + 期望的变化字段」，断言解析结果真的改了对应字段（对应上面第 4 条的 staleness 检测）。
5. 把「评审误放行率」搬到我们的"折中/协商"环节：多角色偏好折中后，用硬约束真值衡量"折中结果是否违反任一角色的硬约束"，统计漏检率。

### 8. 让用户能纠正 AI：从"确认卡"到"拖拽 + 撤销/重做 + 一键回退"

**它的做法**（这是用户可见价值最高的一块）：
- **先确认再开跑**：自然语言 → 结构化 Planning Brief（目的地/日期/预算/偏好/约束），**只有用户明确确认才创建正式规划 Run**（`docs/conversation-entry-migration.md:8`）。
- **记忆的审批制**：记忆分 `candidate` / `active`，用户可 approve / edit / delete；前端明确显示**来源**：`来源：${fact.source_kind === "manual" ? "你手动添加" : fact.source_kind === "legacy" ? "旧画像迁移" : "旅行对话"}`，候选记忆显示「途途从对话中推测了这条习惯，请你确认。」（`frontend/pages.jsx:2073`）。
- **行程可编辑**：SortableJS 拖拽换序（时段留在位置上不跟卡走）、高德搜索弹层换点/加点、直接编辑时间、完整撤销/重做栈 + `beforeunload` 离开守卫；保存走 `PUT /api/plan/{id}/timeline`，**服务端按 haversine 重算每段距离为准**（前端实时显示同公式但不落库），并对残缺 `location` 做防御避免 `KeyError`（`plan_routes.py:421`；README 亮点 13）。
- **路线优化可回退**：`POST /api/plan/optimize_day` 暴力枚举当天景点全排列（**只算景点间 haversine，餐厅不参与评分**，evening 景点固定末位），保证 `best_km ≤ original_km`，差距 < 0.05km 标记 `improved=false`；`POST /api/plan/revert_day` 一键回退，前端在首次优化时保存原始 timeline 快照（README 亮点 11）。
- **修改不重来**：对已有行程提意见时走**迷你图**——跳过 intent/景点搜索，从上次规划的 checkpoint 恢复，只跑 `Planner ⇄ Reviewer（≤2 轮）→ 餐饮 → Finalize`（`graph.py:186-208`）。
- **AI 有异议时可以打断**：若 Planner 认为用户的修改意见会严重降低质量，把顾虑写进 `modification_concern`，图上用 LangGraph `interrupt()` 暂停并**真的问用户**「确认继续修改，或补充新的修改要求」，用户回复会被拼进 `route_modify_opinion`（`graph.py:211-231`）。这是很高级的 human-in-the-loop 设计。
- **Reviewer 的产出分两种读者**：`route_modify_opinion`（给 Planner 的技术诊断）和 `issues`（**给用户看的友好提醒**），并在 schema description 里明令 issues「禁止使用『违规』『冲突』『不合理』『地理跨度过大』这类批判性/技术性词汇」（`schemas.py:88-118`）。

**为什么好**：它把"AI 说错了怎么办"拆成了**四层可撤销**：确认卡（还没跑就拦）、编辑态（跑完了能改）、回退（改了能退）、打断（要改坏了先问）。而且 `issues` 的"双读者"设计，本质是**把内部诊断语言与用户语言分离**——这正是"去 AI 化"排版之外更重要的一层去 AI 化。

**落到 OmniRoute**：我们前端已经在做"去 AI 化"报表/批注风格和核对面板。建议补三件它做得具体的事：
- **`interrupt` 式的异议确认**：当二次增量解析发现"用户要求会打破硬约束"（如"不想去 X，改成免费景点"但那天唯一免费景点距其他节点 40km），不要把决定权全给 LLM，而是把 `concern` 抛给用户确认。可以用我们的 WebSocket 房间直接做，不必引 LangGraph interrupt。
- **回退快照**：我们的"候选池替换（换点/平替）"必须**在服务端保存替换前的原始版本**，前端才能一键回退到"协商原始结果"。它这条 `revert_day` + 前端快照的组合值得照抄。
- **双读者文案**：把 `ai-service` 的内部诊断（硬约束违反了哪条、哪段没补上）与前端展示文案**在 schema 层面分开字段**，并在字段描述里写明禁止的技术词汇。这能防止"未核实/违规"这类词直接漏到用户界面。

---

## 明确不建议借鉴 / 与我们的诚信口径冲突

### A. 价格字段"有值就当真值"展示：`cost` 直接渲染成 `¥xx/人`，没有来源/核实等级

**冲突点**：这是与本项目诚信口径**最直接冲突**的一条。
- 后端把高德 `biz_ext.cost` 原样透传、**没有任何来源分层**：`poi.py:269, 280`（景点）与 `helpers.py:527, 550`（餐厅）都只是 `str(biz_ext.get("cost","")).strip()`，`None` 时给 `None`。
- 最终行程里景点和餐厅都带 `cost` 字段（`nodes.py:827` 景点、`meal["lunch"]/["dinner"]` 展开含 `cost`）。
- **前端直接把它渲染成货币事实**：`frontend/components.jsx:275-289` 的 `formatPoiCost()`——`if (/^\d+(?:\.\d+)?$/.test(raw)) return `¥${raw}/人`;`，空值/`未知`/`暂无`/`[]`/`null` 才返回「票价未提供」，`^0+(\.0+)?$` 返回「免费」；调用点见 `components.jsx:306, 347, 480`、`frontend/api.js:404`（`💰 ¥${cost}/人`）、`frontend/edit.jsx:79, 143`。
- **关键问题**：没有任何地方区分「这个数字来自高德」还是「这个数字来自 LLM」。我逐仓库 grep 了 `价格|门票|预算|花费|人均|ticket|price|cost|budget`，**没有找到任何"已核实/估算/未核实"三级模型**；也没有任何地方给价格字段标注来源或置信度。同时它给数字统一加 `/人` 后缀——而 `biz_ext.cost` 的语义在景点与餐厅上并不一致（景点多为门票、餐厅为人均），这个后缀是**系统自己加上去的解释**，不是数据源给的。项目自己的设计文档也直接把它叫「💰 门票」（`docs/superpowers/specs/2026-06-13-trip-detail-enhancements-design.md:99`）。
- **更严重的是"预算"完全是个装饰**：`trip_budget` 只被 echo 到 `final_plan` 和在摘要里显示，**从未进入任何规划 LLM 的 prompt**，也没有任何合计或超支校验（详见下方"未覆盖处"第 3 条）；而 `budget_style` 这个约束在覆盖率模型里被判为 `"applied"`。**也就是说：一个从不参与决策、从不被校验的预算数字，和一组从未被核实的价格，被并排展示给用户。**

**我们该怎么做**：OmniRoute 的三级价格模型是对的，**不要学它"有值就渲染"**。更进一步，我建议在核对面板上不仅显示"已核实/估算/未核实"，还要显示**核实时点**（供应商快照的时间戳）——因为高德 `cost` 这类字段本身会过期，而 FloatTrip 完全没有时效概念。

### B. 把"规划器"和"事实提供者"的边界画在了数据源一侧，但没画在数值一侧

**冲突点**：它对**景点名**极其严格（封闭池 + 硬失败），对**数字**却完全放任——`open_time`、`rating`、`cost` 都是"有就透传"，`open_time` 的解析甚至只认一种格式。这造成一种**不对称的严格**：名字不能编，数字可以随便来。

**具体证据**：
- 开放时间的确定性校验 `helpers.py:258-288` 用正则 `(\d{1,2})[:：](\d{2})\s*[-~—至]\s*(\d{1,2})[:：](\d{2})` 提取区间；**`open_time` 里不含 `HH:MM-HH:MM` 形态时（如"周一闭馆"、"09:00-12:00,13:30-17:00"、"全天"）直接 `continue` 跳过**（`:275-276`），也就是"解析不了就当合法"。
- `code_graders.py:87-88` 用同一个正则建 `close_map`，**取不到就默认 `24*60`**，于是"夜间不开放"这条检查对解析失败的景点自动放行。
- `filter_by_rating()` 的注释直说「**无评分视为不达标**」（`helpers.py:199`），把"数据缺失"和"评分低"合并成了同一个结果——这与我们「未核实绝不当作 0 元/不达标」的口径方向相反，**缺失应当是一个独立的、对用户可见的状态，而不是被静默丢弃**。

**我们该怎么做**：保持我们的口径——`open_time` 解析失败应该是 `unverified` 并通过门禁降分 + 前端提示，而不是 `continue`；`rating is None` 应该进"未核实"桶展示，而不是当成不达标过滤掉。

### C. 不要学它"用 LLM 复核 LLM 就能保证事实"的乐观假设

**冲突点**：`time_check` 节点是**纯 LLM** 的事实核查器（`nodes.py:479-505`）。它把「开放原文」字符串喂给 LLM，让 LLM 判断时段是否合法；唯一确定性实现 `open_time_violations()` **在主流程里根本没有被调用**（只在 `code_graders` 里被 import 过）。README 亮点 8 也把这件事描述为"CoT 推理（先写完整逐景点推理过程，再从结论中筛选违规）"。
- 后果之一：`time_check` 的结果**没有被任何确定性校验反查**——G2 检查的是 `state.time_violations` 为空，也就是**在检查"LLM 有没有说没问题"**，而不是"时间是否真的合法"。
- 后果之二：达到 `max_time_check_rounds`（默认 3）后「带剩余问题前进」（`nodes.py:462-466`），而**这些残留的 `time_violations` 不会进入最终 payload**——`_finalize_impl` 只把 `reviewer_issues` 写进 `route_issues`，注释明说 time_check 的 violations「不属于注意事项」（`nodes.py:874-878`）。**用户拿到了一份可能含时间冲突的行程，且界面不会告诉他**。

**我们该怎么做**：事实类校验必须是**确定性函数做主判、LLM 只做补充理解**（例如"周一闭馆"这种非结构化文本可以交给 LLM 解析成结构化规则，再由代码比对）。而且**凡是没修完的问题，必须出现在最终 payload 里**——这正好对应我们核对面板要说的"哪段还没补上"。

---

## 它的不足 / 未覆盖处（我们不必重复踩的坑）

以下均基于仓库代码与文档的**实际缺失**，不是推测：

1. **完全没有住宿/酒店数据**。`finalize` 里硬编码一句：`"住宿偏好已作为出行建议保留；当前版本未接入酒店搜索或预订数据。"`（`nodes.py:865-869`），且只对 `category == "accommodation_preference"` 生成。前端只有一个"记录酒店名称、价格…"的自由文本输入框（`pages.jsx:1848`）→ **酒店价格是用户手打的自由文本**，没有任何比价或核实。我们做「住宿夜数」硬约束 + 酒店来源核实，是明确超出它的范围。
2. **没有跨城/长途交通**。grep `跨城|城际|高铁|火车|机票|航班` 在 `app/` 下**零命中**。它只做**市内步行/驾车**：`GET /api/route/walking` 代理高德 `v3/direction/walking`（`plan_routes.py:576+`），前端加载 `AMap.Driving`/`AMap.Walking` 插件（`api.js:343`）。**它不比较出行方式，也不提供任何票价**。我们「跨城出行方式比较 + 票价无可核实来源就标未核实」是独有的。
3. **没有长途分段生成**。`days` 是单一线性循环，没有"按 7 天分段生成与修补"的概念。相关状态里也**没有 `trip_budget` 的数值化使用**——`TravelPlanState` 里唯一与钱有关的字段就是 `trip_budget: Optional[str]`（`schemas.py:164`），它只被 echo 进 `final_plan["trip_budget"]`（`nodes.py:862`）和在需求摘要里显示（`chat/service.py:307-308`）。**`trip_budget` 从未进入任何一个规划 LLM 的 prompt**：planner/reviewer/meal/spot_tips 只注入 `effective_constraints` 中按类别筛出的 `budget_style` 文本（`nodes.py:291 / 381 / 630 / 748`）；它既没有被 `normalize_brief_data()` 转成约束（`planning_constraints.py:57-92`），也不在 `constraints_for_prompt()` 里。**没有任何合计、预算校验或超支提示**。更讽刺的是 `budget_style` 在 `_coverage()` 里被判为 `"applied"`（`planning_constraints.py:114, 129`），尽管代码从不校验任何价格。而且候选池喂给 LLM 的格式 `_spot_line()` **只包含 名称/区域/评分/开放时间/坐标，根本不包含 `cost`**（`helpers.py:210-219`）——即 **Planner 是在看不到任何价格的情况下做路线决策的，却在输出里带上价格给用户看；"预算"只是一个装饰性字符串**。这是它最大的设计裂缝。
   > 附带证据：项目自己的设计文档把 `cost` 直接称作「💰 门票」（`docs/superpowers/specs/2026-06-13-trip-detail-enhancements-design.md:99`），而前端对景点与餐厅共用同一个 `formatPoiCost()` 并统一加 `/人` 后缀——**系统自己在给一个未分级的第三方数字赋予语义**。

4. **封闭池校验不阻断交付——幻觉景点会进入最终行程。** `unknown_spots()` 的结果只是喂给 Reviewer、并在 prompt 里要求它 `approved=False`（`nodes.py:353, 392`），**但没有任何代码在 finalize 前阻断**。一旦 `review_round > max_review_rounds`，流程照样走 `time_check → meal_search → finalize`；此时 `_finalize_impl` 用 `info = spot_info.get(spot["name"], {})`（`nodes.py:813`）取元数据，于是**越界景点会带着 `rating/open_time/location/cost` 全为 `None` 正常出现在最终行程里**（`nodes.py:814-828`），前端渲染成一张没有评分、没有票价、也缺 `dist_from_prev_km` 的卡片。**它检测到了幻觉，却没有把检测结果用于阻止幻觉交付**——门禁只用来打分，不用来拦截。我们必须反过来做：硬门禁失败要么阻止交付，要么强制带上醒目的降级标记。
5. **没有多人协作、没有分享、没有导出**。全仓库 grep `websocket|协作|分享|导出|pdf|ics|calendar`：唯一的"分享行程"是 `mobile-prototype/src/Prototype.tsx:512` 里一个 `onClick={() => {}}` 的空壳按钮（纯视觉原型，非生产代码）。移动端 README 的「当前边界」也明写「首版不包含……社交分享」（`mobile-app/README.md:110`）。**它的"协作"是"人和 AI 多轮对话"，不是"多人同房间"**——我们的 gateway/房间/WebSocket 是它完全没有的维度。
6. **评测夹具不在仓库里**。`.gitignore` 有 `tests/eval/fixtures/`、`tests/eval/transcripts/`、`tests/eval/data/`、`tests/eval/sweep_results/`（另外还有 `eval*` 这个过宽的规则）。`git ls-files tests/eval` 确认**只有 10 个 .py 文件被跟踪，没有任何 fixture JSON**。而 `capture_pool.py` 生成骨架需要真实高德 Key，且 `indoor` 标签必须**手工标注**、天气必须**手工构造**（`EVAL_GUIDE.md:138-156`）。→ **"可复现的评测"实际上无法开箱复现**，想复现得自己花 API 钱重建数据集。我们可以做得更好：把 fixture 直接提交进仓库。
7. **文档与代码已经漂移**。`tests/EVAL_GUIDE.md:61-73` 仍在讲 `G1–G7`、G2="开放时间"、G3="地理跨度"；但 `code_graders.py:1-10` 已经是 **G1/G2/G4/G5/G6/G7/G8**，**G3（地理跨度）已从代码中移除**，G2 语义也改成了"time_check 干净"，README 徽章还写着 `pass@k / pass^k` 而 guide 说 `objective_pass = G1–G6`、代码里是 5 个 key（G1,G2,G4,G5,G6）。**单一文档与实现不同步**。
8. **服务端零 CI**。唯一的 workflow 只覆盖移动端（`mobile-ci.yml`），路径过滤还限定在 `mobile-app/**`、`app/**`、`requirements.txt`。16 个 Python 单测**没有任何自动化执行保障**，而 `requirements.txt` 连 `pytest` 都没有。
9. **前端 `pages.jsx` 单文件 116KB**（`components.jsx` 27KB、`tweaks-panel.jsx` 25KB），无构建、浏览器内 Babel。虽然 README 强调"无构建"是优点，但对一个 11 万字符的文件做修改，协作与 review 成本极高。我们已有 Next.js + TS 的分层，不需要回头。
10. **存在"删掉的 grader 语义"和"未使用的纯函数"**：`open_time_violations()` 被 `code_graders` import 但生产主流程不用（`code_graders.py:17-22` 同时 import 了 `haversine_km`/`spot_location_map` 却从不调用，是 G3 被删除后的残留）；`filter_by_rating` 把无评分 POI 当不达标丢弃；`restaurant_to_dict` 的 `_lookup`（`nodes.py:658-669`）用**双向子串匹配**把 LLM 返回的餐厅名映射回候选池，`name in key or key in name` 会把"南京大牌档"匹配到"南京大牌档（德基广场店）"这类同名不同店——**餐厅名这条线没有像景点那样严格**，是个隐性幻觉入口。
11. **单节点运行时天花板已自我声明**：`docs/agent-runtime.md:62-66` 与 `docs/deferred-runtime-work.md` 明确列出未做多节点任务领取、无分布式 SSE fan-out、无 exactly-once、无 Prometheus/OTel。**它自己承认这是单机玩具级部署**（`RUNTIME_PLANNING_CONCURRENCY` 默认仅 2）。我们 Go gateway + 房间协作的架构在并发维度上领先，不需要参考它的运行时。
12. **License 缺失**（见上表）。如果我们打算借鉴/搬运它的代码片段，先要解决授权问题——目前**没有授予任何权利**。
13. **测试策略有自我矛盾**：`tests/test_architecture_boundaries.py:28-38` 断言 `app/chat/{graph,service,executor}.py` **不得 import `re`**，注释是「no rule-based language fallback」——即禁止任何正则风格的对话语义兜底。这个原则本身可敬，但同一个仓库里 `planning/helpers.py` 大量使用正则解析时间、`reviewer_reliability.py:20` 用正则拆对话轮次。**边界测试画在了 `app/chat` 一个包上，而真正脆弱的正则解析都在 `app/planning`**——门禁建错了地方。这提醒我们：架构边界测试要钉在"数据完整性关键路径"上，而不是钉在某个目录上。

---

## 来源清单（实际打开/读取过的 URL 与本地路径）

**网络**
- [https://github.com/shouzhuoshouzhuo/FloatTrip](https://github.com/shouzhuoshouzhuo/FloatTrip)（`web_fetch` 在本机被网络策略阻断，未成功打开；仓库内容改由 `git clone` 获取）
- [https://raw.githubusercontent.com/shouzhuoshouzhuo/FloatTrip/main/README.md](https://raw.githubusercontent.com/shouzhuoshouzhuo/FloatTrip/main/README.md) — HTTP 200
- [https://raw.githubusercontent.com/shouzhuoshouzhuo/FloatTrip/main/requirements.txt](https://raw.githubusercontent.com/shouzhuoshouzhuo/FloatTrip/main/requirements.txt) — HTTP 200
- [https://raw.githubusercontent.com/shouzhuoshouzhuo/FloatTrip/main/LICENSE](https://raw.githubusercontent.com/shouzhuoshouzhuo/FloatTrip/main/LICENSE) — **HTTP 404**（README 声称 MIT）
- [https://ungh.cc/repos/shouzhuoshouzhuo/FloatTrip](https://ungh.cc/repos/shouzhuoshouzhuo/FloatTrip) — HTTP 200，GitHub 仓库元数据（stars/forks/createdAt/pushedAt/defaultBranch）
- [https://github.com/shouzhuoshouzhuo?tab=repositories](https://github.com/shouzhuoshouzhuo?tab=repositories) — fetch 失败（未取到用户仓库列表；拼写相近仓库未能枚举，但 `FloatTrip` 本身可直接 clone，故不影响结论）
- [https://codeguilds.dev/packages/floattrip](https://codeguilds.dev/packages/floattrip) — HTTP 503，无可读内容
- 搜索：[`web_search "FloatTrip github 旅行 规划"`](https://www.bing.com/search?q=FloatTrip+github+%E6%97%85%E8%A1%8C+%E8%A7%84%E5%88%92)（返回结果均为外部未验证数据，未采信）

**本地 clone（`%TEMP%\FloatTrip_research`，`main` @ `ec911f7`，`git fetch --unshallow` 后 56 提交）** — 通读/定位过的文件：
`README.md`、`requirements.txt`、`.gitignore`、`.github/workflows/mobile-ci.yml`、
`run.py`、`app/main.py`、
`app/planning/schemas.py`、`app/planning/prompts.py`、`app/planning/graph.py`、`app/planning/nodes.py`、`app/planning/helpers.py`、`app/planning/runtime_worker.py`、
`app/core/planning_constraints.py`、
`app/providers/amap/poi.py`、
`app/api/plan_routes.py`、`app/api/runtime_routes.py`、`app/api/sweep_routes.py`、
`frontend/components.jsx`、`frontend/pages.jsx`、`frontend/edit.jsx`、`frontend/api.js`、`frontend/chat-state.js`、
`mobile-app/README.md`、`mobile-app/src/native/QZAMapView.tsx`、`mobile-app/src/screens/TripMapScreen.tsx`、`mobile-prototype/src/Prototype.tsx`、
`tests/EVAL_GUIDE.md`、`tests/eval/graders/code_graders.py`、`tests/eval/graders/llm_judge.py`、`tests/eval/graders/reviewer_reliability.py`、`tests/test_architecture_boundaries.py`、
`docs/project-introduction.md`、`docs/agent-runtime.md`、`docs/conversation-entry-migration.md`、`docs/deferred-runtime-work.md`、
`openspec/specs/*`（12 个规格文件，已枚举）、`docs/superpowers/specs/*`（已枚举）。

**仓库中未找到**（已 grep 确认）：任何价格核实等级/来源标注字段；住宿/酒店搜索与预订；跨城/城际交通与票价；多天分段生成；多人协作/房间/WebSocket；PDF/ICS/日历/分享导出；Python 服务端 CI；被跟踪的评测 fixture JSON；`LICENSE` 文件；`pyproject.toml`/`pytest.ini`。
