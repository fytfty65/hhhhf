# ContextualLobby.tsx 拆分计划（跨会话交接单）

> 这份文件是拆分任务的**唯一持久记忆**。任何新会话接手时：先读本文件 → 跑一次门禁确认基线是绿的 → 按「批次路线图」继续。
> 目标文件：`front/app/ContextualLobby.tsx`（拆分前 **3,991 行 / 218,225 B**，含 21 个顶层定义、25 个本地组件）。

## 1. 目标与硬约束

把巨型单文件按 **叶子 → 根** 逐批拆到 `front/app/components/` 下，使其只保留状态与编排。

- **硬约束 A：功能零损失** —— 所有界面、面板、按钮、文案、props 签名、Tailwind 类名逐字保留。
- **硬约束 B：前端不受损** —— 首屏体积不得变差，E2E 必须全绿。
- 一批只做**搬运**，不做行为修正；行为修正单独提交。
- 无法保证零损失时**如实报告并停止**，不允许伪造结果。

## 2. 基线（用于验收，2026-09-16 实测）

| 指标 | 基线值 |
|---|---|
| `ContextualLobby.tsx` | 3,991 行 / 218,225 B |
| 首屏 JS | **1,105.5 KB（13 个 chunk）**（2026-09-17 经用户确认由 1,104.6 → 1,105.0 KB；2026-09-18 更新到 1,105.5 KB：核对面板陆续加了「本次调整 / 价格核对 / 跨城怎么走 / 长途分段 / 自动复核 / 诉求核对 / 数据降级」与流式 JSON 解析修复，面板本体走 dynamic 按需加载，差额来自宿主接线） |
| 首屏 CSS | 141.1 KB（同上：面板用到的 Tailwind 类进全局样式） |
| 单测 | 79 个（`app/lib/*.test.ts`，9 个文件） |
| E2E | `refactor-regression.spec.ts` 7 个 + `core-flow.spec.ts` 1 个 + `plan-governance.spec.ts` 1 个 = **9 个** |

**验收**：拆分后首屏 JS ≤ **1,105.5 KB**，9 个 E2E 全绿，`ContextualLobby.tsx` 只剩状态与编排。

## 3. 进度

| 批 | 内容 | 状态 |
|---|---|---|
| — | 行为基线 + 安全网 `front/e2e/refactor-regression.spec.ts` | ✅ 提交 `ebd2d90` |
| 1 | `TravelModeCard` + `PreferenceCard` → `components/SelectCards.tsx`（38 行） | ✅ 提交 `65c554c`（tsc / 79 单测 / build / 8 E2E 全绿） |
| 2 | `BottomMapCarousel`(96) → `components/BottomMapCarousel.tsx`；`ExpandableConnectorNav`(165) → `components/ExpandableConnectorNav.tsx` | ✅ 提交 `4671f07` + `90d4a50`（**单组件一提交**，两次门禁全绿） |
| 3 | `DynamicBudgetCard`(53) → `components/DynamicBudgetCard.tsx`；`DeductionPanel`(79，含 `DeductionPanelProps`) → `components/DeductionPanel.tsx`；`EditIntentModal`(37) → `components/EditIntentModal.tsx` | ✅ 提交 `2795e34`（三个叶子组件一次提交，门禁全绿） |
| 4 | `DraftingPanel`(168，含仅它使用的 `TRAVEL_MODES`/`USER_ROLES`) → `components/DraftingPanel.tsx`；`TeamPresenceBar`(234，含仅它使用的 `ActivityEvt`) → `components/TeamPresenceBar.tsx` | ✅ 提交 `9130450`（门禁全绿） |
| 5 | `getCleanPhotoUrl` → `lib/lobbyUtils.ts`（具名导出）；`PoiImage`+`PoiImageProps`(123) → `components/PoiImage.tsx` | ✅ 提交 `9b22dee`（门禁全绿；**顺序已调整**，见下） |
| 6 | `MafengwoStylePanel`(403) → `components/MafengwoStylePanel.tsx`；宿主随之清理 10 个失效 import | ✅ 提交 `6cadefd`（门禁全绿） |
| 7 | `shortenRegionName`/`tryExtractJson`/`normalizeLnglat`/`extractStreamingRoutes` → `lib/lobbyUtils.ts`（具名导出）；`OmniLogo`(21，纯 SVG) → `components/OmniLogo.tsx`；`AuthPortalScreen`(232) → `components/AuthPortalScreen.tsx` | ✅ 提交 `1977fe3`（门禁全绿） |
| 8 | `UnifiedWorkspace`（1,357 行，占宿主 60%）按面板继续拆 | ⛔ **用户 2026-09-18 决定不拆，本项关闭** |
| 收尾 | 体积/行数对比 + 全门禁复跑 | ✅ 见下（批 7 后已完成一次） |

**顺序调整说明**：原计划批 5 是 `MafengwoStylePanel`。依赖分析发现它引用了仍在宿主里的 `PoiImage` 与 `getCleanPhotoUrl`，先抽它会形成 `ContextualLobby ⇄ MafengwoStylePanel` 循环导入，故改为先抽叶子（PoiImage + 工具函数），再抽 `MafengwoStylePanel`。

（行号为拆分前行号，每次抽取后会漂移；以组件名/函数名定位为准。）

### 收尾对比（批 1–7 完成后，2026-09-17 实测）

| 指标 | 拆分前 | 现在 | 结论 |
|---|---|---|---|
| `ContextualLobby.tsx` | 3,991 行 / 218,225 B | **2,202 行 / 123,422 B** | -45% 行数 |
| ├ 根组件 `ContextualLobby` | （混在一起） | 730 行 | 仅状态与编排 |
| └ `UnifiedWorkspace` | 1,354 行 | 1,357 行 | **尚未拆（待确认）** |
| 首屏 JS | 1,104.6 KB / 13 chunk | 1,104.5 KB / 13 chunk | 无劣化 |
| 首屏 CSS | 140.2 KB | 140.2 KB | 无劣化 |
| 单测 | 79 | 79 passed / 0 failed | 无损失 |
| E2E | 8 passed | 8 passed（含 core-flow 真实注册/登录） | 无损失 |
| 生产构建 | exit 0 | exit 0 | 无损失 |

已抽出：**12 个组件文件**（`SelectCards`/`BottomMapCarousel`/`ExpandableConnectorNav`/`EditIntentModal`/`DeductionPanel`/`DynamicBudgetCard`/`DraftingPanel`/`TeamPresenceBar`/`PoiImage`/`MafengwoStylePanel`/`OmniLogo`/`AuthPortalScreen`）+ **`lib/lobbyUtils.ts`（5 个纯函数）**。

**已知待办 / 清理记录**
1. ✅ 已完成（提交 `137a924`）：宿主内重复的 `Phase`/`UserProfile`/`RoomMember` 已删除，改为 `import type { ... } from './types'`（与 `app/types/index.ts` 逐字一致；已确认没有任何文件从 ContextualLobby 导入这些类型）。宿主 2,264 → 2,242 行。
2. ✅ 已完成（提交 `137a924`）：移除宿主里既有的死 import `AvatarUploader`（`ebd2d90` 版本核对确认拆分前即未使用）。**组件本身仍被 `components/ProfileScreen.tsx` 使用**，不是死代码，未删文件。
3. ✅ 已完成（提交 `0623d0e`）：4 处与规范定义**逐字一致**的副本已去重 —— 宿主 `ProfileTrip`/`CommunityComment`、`components/PoiImage.tsx` 的 `PoiImageProps`、`components/TeamPresenceBar.tsx` 的 `ActivityEvt`；同时把 `PoiImageProps` 的 8 条字段注释并入 `app/types/index.ts`（纯注释，避免丢文档）。宿主 2,242 → 2,219 行。
4. ✅ 已完成（提交 `97a59b0`）：收尾类型清理 —— ① 修复上一轮我自己引入的回归（宿主 type import 里 `CommunityComment`/`ProfileTrip` 实际未使用）；② 删除宿主的死类型 `interface CommunityPostItem`（这三个类型在拆分前的 `ebd2d90` 里就都是"只有定义、从无引用"的死代码）及其孤立区块注释；③ `components/DeductionPanel.tsx` 的本地 `DeductionPanelProps` 去重为规范定义（规范版多一个可选字段 `reasoningSteps?`，编译期放宽、运行时无影响）。宿主 2,219 → 2,202 行，且宿主与本次改动文件经脚本复检**均无未用 import**。
5. `work/` 下的抽取脚本（`extract-split2.ps1`、`move-to-lib.ps1`、`cleanup-imports.ps1`、`fix-imports7.ps1`、`extract-auth.ps1`、`dedupe-types*.ps1`、`cleanup-types-final.ps1`）是本次拆分的工具，`work/` 已被 gitignore，不会进仓库；续做时可复用。**脚本编写注意**：插入换行必须写 `${eol}`（写 `$eolxxx` 会被当成变量名），多行字面量要同时兼容 LF/CRLF，`git commit -m` 传多行消息在本机 PowerShell 下会被解析坏，改用 `git commit -F 消息文件`。

> **清理项已全部完成。`UnifiedWorkspace`（1,357 行）用户已于 2026-09-18 明确「不用拆」→ 拆分任务到此结束，不再有未决项。** 后续工作转为规划质量与产品能力（见下「功能批次」）。

### 功能批次（拆分任务之后，同一门禁协议）

| 批 | 内容 | 提交 |
|---|---|---|
| F1 | 评测集先行：`ai-service/tests/eval/golden_cases.json`（29 例）+ `tools/eval_plan_quality.py` | `ef83c39` |
| F2 | 行程骨架/餐饮住宿下限 + 三级预算模型（已核实/估算/未核实） | `7ff3fb8` |
| F3 | 候选池（高德 POI → 候选，含价格来源与 tier） | `e103633` |
| F4 | 二次增量解析（排他/配额/升降档）+ 响应度度量 | `7abc5e3` |
| F5 | 长途（≥14 天）分段：只重生成失败段 | `a4035fa` |
| F6 | 升/降档排序策略 + 基线更新 | `e87b78b` |
| F7 | 价格来源分级与合并（弱来源不覆盖强来源 + TTL 过期不采用） | `39db2a0` |
| F8 | 分段修补接进管线 | `97ce685` |
| F9 | 出行方式比较层（合适 + 便宜，票价没有来源就标未核实） | `f062fee` |
| F10 | 跨城腿识别 + 出行审计随 payload 下发 | `8942499` |
| F11 | 前端渲染四块新 payload（本次调整 / 价格核对 / 跨城怎么走 / 长途分段） | `a1021ac` |
| F11b | 面板从「不可滚的 header」挪进滚动区（否则 8 段内容把正文挤成 0 高、后几段滚不到）+ E2E 加版面守卫 | 见下 |
| F12 | 长途（≥14 天）**首轮按 7 天分段生成**（`core/long_trip.build_segmented_plan` + agent.py 薄接线），既有修补退化为兜底 | `ad9f6d0` |
| F13 | 评测集 29 → 38 条（30/45 天长途、二次换点/配额/升档/换城市、跨城最划算、价格诚信）+ `horizon` 真正参与机器判定 | `a7ec892` |
| F14 | **Critic/Repair 闭环**（`core/plan_review.review_plan`：propose→critique→repair→rescore，≤3 轮，掉门禁/掉分即回滚，无改动即停）+ 面板「自动复核」 | `d73c3d5` |
| F15 | **诉求逐条核对**（`core/constraint_coverage`：稳定 id + 五态 applied/partial/unverified/advisory/missing，含"说改但没改"机械检测）+ 面板「诉求核对」端到端渲染 | `29b7978` |
| F16 | 评测报告新增 **`g_no_invention`** 节（逐条点名"写了数值却没有来源"的字段）+ `--max-unsourced` 可选门禁 + **离线夹具进仓库**（`tests/eval/fixtures/`，无 Key 无网络可复现） | `e17c358` |
| F17 | **候选池接进 Repair 闭环**（空天/缺玩点/缺餐/住宿夜数/必去项都能用真实候选补上；无池不编造）+ 复核与治理**共用一份候选池**（一次取数、`POOL_FETCH_TIMEOUT_SECONDS=12s`） | `ae81033` |
| F18 | **数据降级登记处**（`core/degradation`：数据源注册表 + 从 payload 推导 `{source,label,status,reason,impact}`）+ 面板「数据降级」统一渲染（文案只在后端一处定义） | `16569e6` |
| F19 | 地图底图修复：备用底图换成**实测可达**的 Esri 街道图（OSM 在部分网络 8s 超时）、误判不再不可逆、状态条挪出左下角（原先被行程节点卡片条盖住）+ E2E「读得到」守卫 | `b4c8e1c` |
| F20 | 地图**离线示意图兜底**（`lib/routeSchematic.ts` + `components/RouteSchematicMap.tsx`：一块瓦片都画不出来时按经纬度画相对位置图，零网络依赖）+ 状态条定位到不受任何浮动元素影响的位置 + E2E 断言"不被遮挡/不压控制条/示意图可用" | `f064541` |
| F21 | **坐标断链修复**：`get_dynamic_pois` 只回 `location` 字符串、下游只认 `lnglat` 数组 → 整池 0 条坐标 → 方案全无坐标、地图空白；改为经 `core/poi_pool.poi_record_from_amap`（纯函数+单测）同时给两者。另修 `types` 参数压掉关键词相关性（搜"博物馆"返回购物中心） | 本批 |

**F11 附带修掉一个真 bug**：宿主流式累积时用 `replace(/null/g, "")` 清洗 token，会把**合法 JSON 里的 null**（票价未核实就是 `price:null`）删成 `"price":`，导致整段 `[FINAL_JSON]` 解析失败——后果是 `quality`/`budget_report`/四块新数据全部丢失、核对面板整块不显示。现在改为**先按原文解析**（我们下发的 JSON 一定合法），解析不出来才退回"删 null"的兜底（那是给模型吐字夹带的 null 准备的）；展示用的清洗仍在 `humanReadableLogs`。E2E mock 里刻意保留 `price: null` 作为回归哨兵。同时 `final_route` 消息路径也接入同一份 payload（重连/回放只收到它时面板同样有数据）。

**F11b 版面修复**：核对面板原来挂在左侧栏的 `header` 里，而 header 是 auto 高度 + 不可滚动；8 段内容把整列顶到 1210px（1440×900 实测：header scrollHeight=clientHeight=1210、正文区 `flex-1 overflow-y-auto` 的 clientHeight=**0**、文档不可滚），结果①行程正文完全看不见②面板最后几段滚不到。修法：面板移进滚动区顶部（`px-5 pt-2 sm:px-8` 包一层，紧跟 `<header>` 之后），与行程正文一起滚。E2E 加了版面守卫（找元素真正所在的可滚动祖先 → `scrollIntoView({block:'center'})` → 断言滚得进视口 + 滚动区 clientHeight > 200），这个 bug 再也回不来。

其余顶层定义参考：`shortenRegionName`(57)、`OmniLogo`(105)、`tryExtractJson`(127)、`normalizeLnglat`(157)、`extractStreamingRoutes`(181)、`getCleanPhotoUrl`(228)、`PoiImage`(248)、`AuthPortalScreen`(367)、`ContextualLobby`(640，根)、`EditIntentModal`(1370)、`UnifiedWorkspace`(1408)。

## 4. 每批协议（必须遵守）

1. 开工前跑一次门禁，确认**当前是绿的**（不绿先修）。
2. 抽取：新建 `front/app/components/Xxx.tsx`（含 `'use client'` 如需要），把组件体逐字搬过去并 `export`；原文件删除该定义并 `import` 回来。工具函数放 `front/app/lib/lobbyUtils.ts`。
3. **抽取后必须核对（血的教训）**：列出新文件的顶层定义清单，确认只包含目标定义；确认源文件无残留定义；确认相邻的 `type`/`interface`/`const` 没被区间边界卷进来（批 5 就把紧邻 `PoiImage` 的 `Phase`/`UserProfile`/`RoomMember` 卷走了，靠 tsc 才发现）。同时给新文件补齐它真正用到的 import（`React.*` 用法要记得 `import React`）。
4. 跑门禁：`powershell -NoProfile -File front/scripts/split-gate.ps1`
5. **绿** → 提交 `refactor(split): 抽取 Xxx 到 components/Xxx.tsx`（提交信息里附门禁结果）；**红** → `git checkout -- front/app/ContextualLobby.tsx` 并删除新文件。
6. 绝不攒批：仓库任何时刻都必须是绿的、可发布的；行为修正与类型去重等**非搬运改动单独提交**。

## 5. 环境注意（本机 Windows）

- **DSH 沙箱**：受限模式下 `npm run test` / `next build` 会 `Error: spawn EPERM`（沙箱禁止带管道 stdio 的子进程），是**假失败**。解法：在独立终端跑本脚本，或确认 DSH 文件策略为 `danger-full-access`。
- **`.ps1` 必须带 UTF-8 BOM（血的教训）**：本机默认是 Windows PowerShell 5.1，它读 `.ps1` 时按系统 ANSI 码页解码；文件一旦丢掉 BOM，脚本里的中文串会被解成乱码并连带把引号解析坏，报错却指向毫不相干的行（实测：`[regex]::Matches($html, 'src="([^"]+\.js)"')` 报 `Unexpected token '('`）。DSH 的 `edit`/`write` 工具会把 BOM 去掉，所以**每次用工具改完 `front/scripts/split-gate.ps1`，都要把 BOM 补回去**：
  ```powershell
  $p='front/scripts/split-gate.ps1'
  $t=[System.IO.File]::ReadAllText($p,(New-Object System.Text.UTF8Encoding($false)))
  [System.IO.File]::WriteAllText($p,$t,(New-Object System.Text.UTF8Encoding($true)))   # 需 danger-full-access
  ```
  校验：`[void][System.Management.Automation.Language.Parser]::ParseFile($p,[ref]$null,[ref]$errs)` 应无错。

- **Playwright**：浏览器在 `work/ms-playwright`（脚本已内置 `PLAYWRIGHT_BROWSERS_PATH`），无需联网下载。
- **内存紧张**（本机实测可用内存约 6–7 GB、提交内存已用 37/64 GB）：跑构建/E2E 时**不要同时**跑 DSH 自身的 `pnpm build`/`dev:web`；必要时 `$env:NODE_OPTIONS='--max-old-space-size=4096'`。
- **崩溃恢复**：因为每批一提交，崩溃最多损失当前一批；`work/split-gate.log` 保留上一次门禁结果。
- 历史背景：2026-09-16 期间 DSH 侧出现过一次 `[ELIFECYCLE] Command failed with exit code 3221226505`（= `0xC0000409`，node 侧硬终止，与项目代码无关），当时正卡在批 1 未提交状态，所以本计划强制「一批准一提交」。
- **`0xC0000409` 也会打到我们自己的 E2E**：2026-09-18 有一次 `playwright test` 以 exit `-1073740791`（同一个 `0xC0000409` fail-fast）整段崩掉，**没有任何测试输出**；单独重跑 `npx playwright test` 就 9/9 全过。判别法：**exit 码是 `-1073740791`/`3221226505` 且完全没有测试行 → 环境级崩溃，直接重跑，不要当代码失败去改代码**；只有"有测试行 + 断言失败"才是真失败。
