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
| 首屏 JS | **1,104.6 KB（13 个 chunk）** |
| 首屏 CSS | 140.2 KB |
| 单测 | 79 个（`app/lib/*.test.ts`，9 个文件） |
| E2E | `refactor-regression.spec.ts` 7 个 + `core-flow.spec.ts` 1 个 = 8 个 |

**验收**：拆分后首屏 JS ≤ 1,104.6 KB，8 个 E2E 全绿，`ContextualLobby.tsx` 只剩状态与编排。

## 3. 进度

| 批 | 内容 | 状态 |
|---|---|---|
| — | 行为基线 + 安全网 `front/e2e/refactor-regression.spec.ts` | ✅ 提交 `ebd2d90` |
| 1 | `TravelModeCard` + `PreferenceCard` → `components/SelectCards.tsx`（38 行） | ✅ 提交 `65c554c`（tsc / 79 单测 / build / 8 E2E 全绿） |
| 2 | `BottomMapCarousel`(96) + `ExpandableConnectorNav`(166) | ⬜ 待做 |
| 3 | `DynamicBudgetCard`(54) + `DeductionPanel`(75) + `EditIntentModal`(38) | ⬜ 待做 |
| 4 | `TeamPresenceBar`(233，依赖 `data`/`API_BASE`) + `DraftingPanel`(164) | ⬜ 待做 |
| 5 | `MafengwoStylePanel`(~404) | ⬜ 待做 |
| 6 | `PoiImage`(119) + `AuthPortalScreen`(273) + 5 个纯函数工具 | ⬜ 待做 |
| 7 | `UnifiedWorkspace`（1,354 行）按面板继续拆 | ⬜ **未决，风险最高** |
| 收尾 | 体积/行数对比 + 全门禁复跑 | ⬜ 待做 |

（行号为拆分前行号，每次抽取后会漂移；以组件名/函数名定位为准。）

其余顶层定义参考：`shortenRegionName`(57)、`OmniLogo`(105)、`tryExtractJson`(127)、`normalizeLnglat`(157)、`extractStreamingRoutes`(181)、`getCleanPhotoUrl`(228)、`PoiImage`(248)、`AuthPortalScreen`(367)、`ContextualLobby`(640，根)、`EditIntentModal`(1370)、`UnifiedWorkspace`(1408)。

## 4. 每批协议（必须遵守）

1. 开工前跑一次门禁，确认**当前是绿的**（不绿先修）。
2. 抽取：新建 `front/app/components/Xxx.tsx`（含 `'use client'` 如需要），把组件体逐字搬过去并 `export`；原文件删除该定义并 `import` 回来。
3. 跑门禁：`pwsh -File front/scripts/split-gate.ps1`
4. **绿** → 提交 `refactor(split): 抽取 Xxx 到 components/Xxx.tsx`（提交信息里附门禁结果）；**红** → `git checkout -- front/app/ContextualLobby.tsx` 并删除新文件。
5. 绝不攒批：仓库任何时刻都必须是绿的、可发布的。

## 5. 环境注意（本机 Windows）

- **DSH 沙箱**：受限模式下 `npm run test` / `next build` 会 `Error: spawn EPERM`（沙箱禁止带管道 stdio 的子进程），是**假失败**。解法：在独立终端跑本脚本，或确认 DSH 文件策略为 `danger-full-access`。
- **Playwright**：浏览器在 `work/ms-playwright`（脚本已内置 `PLAYWRIGHT_BROWSERS_PATH`），无需联网下载。
- **内存紧张**（本机实测可用内存约 6–7 GB、提交内存已用 37/64 GB）：跑构建/E2E 时**不要同时**跑 DSH 自身的 `pnpm build`/`dev:web`；必要时 `$env:NODE_OPTIONS='--max-old-space-size=4096'`。
- **崩溃恢复**：因为每批一提交，崩溃最多损失当前一批；`work/split-gate.log` 保留上一次门禁结果。
- 历史背景：2026-09-16 期间 DSH 侧出现过一次 `[ELIFECYCLE] Command failed with exit code 3221226505`（= `0xC0000409`，node 侧硬终止，与项目代码无关），当时正卡在批 1 未提交状态，所以本计划强制「一批准一提交」。
