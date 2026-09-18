# 规划质量路线图（从"生成+校验"到"目标函数+分配+修补+兜底"）

> 背景：用户反馈（2026-09-17）指出三个真实缺口 —— ① 预算不足时无法在预算内尽量满足、也不会给"平替兜底方案"；② 二次增量（如"多吃地道美食"）不会真正改变方案；③ 智能体不会"自己思考"用户需求。
> 本文把这三条落成可实施、可度量的机制。**评测先行**：所有改动都用 `tools/eval_plan_quality.py` + `tests/eval/golden_cases.json` 量化，不允许"看起来做了"。

## 0. 已锁定的产品决定（用户 2026-09-17 确认）

| 决定 | 落地含义 |
|---|---|
| 预算判定：**能取证就取证，不能取证就估算但必须标明** | 价格三级：`verified`（供应商/高德等可核来源）／`estimated`（估算，强制 `estimated: true` 且来源标为估算）／`unknown`。**已验证部分超预算 = 硬失败**；已验证够但"已验证+估算上限"可能超 → `budget_at_risk`（"可能超支"），估算部分单列。**估算永不写进"已验证结论"，也不得把不够说成够。** |
| 兜底尺度：**不动天数/城市** | 兜底动作有界、有序：① 玩点降档 ② 减次要玩点槽 ③ 远点换近点 ④ 住宿降档/换区域 ⑤ 交通降级。"有吃有玩有住"的**存在性最后才动**。若确实必须动天数/城市 → **不自动执行**，返回"需确认提案 + 差异说明"。 |
| 增量范围：**先尽可能满足，做不到才兜底并标明** | 增量先走"全力满足"路径（允许加天数/换城市 = 用户需求）。不可行时才降级，并必须输出 `{不合理点, 采取的动作, 替代方案, 预算差, 保留下来的偏好}` 给用户。 |

## 1. 四个机制

### ① 槽位骨架（把"一体化"变成硬保证）—— `core/itinerary_skeleton.py`
- 每天槽位：`1 个核心玩点 + 0~2 个次要玩点 + 午餐 + 晚餐`；每夜 `住宿`（单日游无住宿）。
- 由 `pace` 决定玩点槽数量（relaxed 少、intense 多），由 `days` 决定住宿夜数。
- 骨架先定，LLM/候选只在槽里填内容 → 结构上不可能出现"没酒店/没餐"。
- 硬门禁：`住宿夜数 >= 天数-1`（跨夜行程）、`餐饮节点 >= 2*天数-1`、`每天 >= 1 个玩点`。

### ② 预算分配器（预算成为一等公民）—— `core/budget_planner.py`
- 候选按**意图标签**聚合分档（`history_museum` / `local_food_street` / `hotel_economy|comfort|quality` …），每档带价格与来源级别。
- 先扣刚性成本（住宿夜数 × 最低价、餐饮次数 × 最低人均、跨城交通），余量按偏好权重分配给玩点槽，每槽选"负担得起的最高档"。
- **不可行判定**：`min_cost(骨架) > 预算` → 触发兜底（见 ③ 动作序）。
- 输出：`{verified_cost, estimated_cost, unknown_count, status: ok|at_risk|over|unverifiable, shortfall, confidence, allocation[]}`。

### ③ 兜底（平替）引擎 —— 与 ② 同模块
- 动作按固定优先级执行，每步产出**可解释的替换记录**：`{slot, from, to, price_delta, reason}`。
- 保留原则：先降档 → 再减次要玩点 → 再换近点 → 最后动住宿档位；**住宿/正餐的存在性不删**。
- 触发天数/城市变更时：不自动执行，返回 `needs_confirmation: true` 的提案。
- 相似度指标：`preserved_intents / requested_intents`（原始意图保留率），用于 `fallback_quality` 维度。

### ④ 增量 = 结构化 delta + 配额（这才叫"真的改变"）—— `core/increment.py`
- `parse_increment(previous_plan, text) -> {add_intents:{local_food:+3}, upgrade:{local_food:quality}, constraints:{}, structural:{days:+1|city:...}}`。
- delta 转成**配额/档位约束**（如 `food_nodes >= 2*days + 3`、"地道/老字号"标签升档），而不是关键词 +0.05。
- 可度量：`increment_responsiveness = 目标特征变化量 / 非目标部分扰动`，越大越好；扰动复用最小扰动重规划思路。

### ⑤ 自评-修补闭环（"自己思考"的工程化定义）—— `core/plan_loop.py`
- `propose → critique → repair → rescore`，最多 3 轮，**分数不升就回滚**到上一轮最优。
- `critique`：`plan_quality.evaluate_plan` + 骨架校验 + 预算可行性 → 按严重度排序的未满足项。
- `repair`：只允许有限动作（补槽/换同类/降档/调时段/换近点），每次动最少必要项。
- 分工：**LLM 负责候选与语言，闭环负责收敛与验证**；不指望提示词让 LLM 自我保证。

## 2. 评测升级（把用户三条变成可自动断言的回归项）

- 新增硬门禁：`integrated_skeleton`、`budget_feasible_or_fallback`（要么预算内可行，要么兜底方案本身也一体化）。
- 新增维度：`fallback_quality`、`increment_responsiveness`、`budget_efficiency`。
- 金标集补专用用例：预算不足的西安亲子、预算不足但必须保留地道小吃、增量"多吃地道美食"、增量"降 20% 预算"、兜底后仍要一体化。

## 3. 实施顺序（每步都用评测集量化）

| 步 | 内容 | 状态 |
|---|---|---|
| 1 | 骨架 + 预算分配器（三级价格模型）+ 两个新硬门禁 | ✅ 完成（提交 `7ff3fb8`）：`core/itinerary_skeleton.py`、`core/budget_planner.py`、门禁接入 + 27 项单测 |
| 1.5 | **接线到真实管线**：`agent.py` 在 `final_route` 下发前调用 `plan_quality_snapshot()`，随 payload 附 `quality` / `budget_report` / `fallback`（受 try/except 保护，失败不影响出方案） | ✅ 完成：业务逻辑在 core（可单测），agent.py 只做薄接线；已通过 `py_compile`、应用导入检查、5 项 snapshot 单测 |
| 2 | 平替库（真实候选池按意图归档）+ `fallback_quality` 维度 + 前端展示 disclosure | 🟡 部分完成：`core/candidate_index.py`（意图归档 / 同类替换 / **排他替换** `apply_exclusions`）已实现并单测；**真实候选池接入**与 `fallback_quality` 维度待做 |
| 3 | 增量 delta + 配额 + `increment_responsiveness`（服务里已有 `is_refinement` 分支可复用） | ⬜ 待做（`apply_exclusions` 已提供"排他+替换"的执行层，缺的是从用户话里解析 delta 并接进 `is_refinement` 路径） |
| 4 | Critic/Repair 闭环（LLM 只当候选生成器） | ⬜ 待做 |

### 用户场景支持现状（2026-09-17 追加）

| 场景 | 支持程度 | 关键实现 |
|---|---|---|
| **一个月（30 天）长途** | 🟡 确定性层已支持 | `itinerary_skeleton.segment_days(30)` → 5 段（7/7/7/7/2）；`horizon_advisories()` 给出"按周分段生成、住宿按城市聚合、每 7 天至少 1 个休整日"；骨架/预算/评分本就按天计算。**缺口**：单次 LLM 生成 150+ 节点不可靠，需要真正的"分段生成 + 段间滚动上下文"（未做） |
| **二次说"不想去这些点，换掉"** | 🟡 执行层已就绪 | `candidate_index.apply_exclusions()`：点名剔除 → 从候选池补同类（保证不重复替换同一候选、一体化不破）→ 候选池没有就如实告知"没换成"、点名地点本就不在方案里也如实告知。**缺口**：候选池接入 + 从用户话里解析 exclude（步 3） |
| **"有山有水" + 吃住怎么安排** | ✅ 已可判定 | 复合意图 `mountain_water`（山、水两组**必须同时命中**，只排山不算满足）；新维度 `anchoring`（当天餐饮/住宿到玩点最近距离 ≤15km，**缺坐标不给满分**），权重 0.15 |

### 已下发给前端的字段（步 1.5）

`final_route.payload` 新增：
- `quality`：`{score, verdict, gate_passed, gate_failures[], unverifiable_count, dimensions{}}`
- `budget_report`：`{budget, verified_cost, estimated_cost, unknown_count, status, shortfall, confidence, note}`
- `fallback`（仅预算 `over`/`at_risk` 时）：`{trigger, shortfall, remaining_shortfall, actions[], substitutions[], dropped[], preserved_intents[], preserved_ratio, needs_confirmation, disclosure}`

> 注意：`verdict=fail` 且 `score` 仍可能很高 —— 硬门禁失败与加权得分是两回事，前端展示时要把 `gate_passed` 放在显眼位置（例如"预算超出 ¥X，已给你平替方案"）。

### 离线可行 / 需联网的验证边界

- 可离线验证：骨架、预算三级模型、兜底动作序、门禁、snapshot 组装 —— 已全部单测覆盖。
- **尚需真实 LLM+数据源验证**：接线后在真实 run 里的字段落地情况（`final_route` payload 是否正常下发、前端渲染、真实候选下的平替质量）。
