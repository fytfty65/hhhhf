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
| 2 | `BottomMapCarousel`(96) → `components/BottomMapCarousel.tsx`；`ExpandableConnectorNav`(165) → `components/ExpandableConnectorNav.tsx` | ✅ 提交 `4671f07` + `90d4a50`（**单组件一提交**，两次门禁全绿） |
| 3 | `DynamicBudgetCard`(53) → `components/DynamicBudgetCard.tsx`；`DeductionPanel`(79，含 `DeductionPanelProps`) → `components/DeductionPanel.tsx`；`EditIntentModal`(37) → `components/EditIntentModal.tsx` | ✅ 提交 `2795e34`（三个叶子组件一次提交，门禁全绿） |
| 4 | `DraftingPanel`(168，含仅它使用的 `TRAVEL_MODES`/`USER_ROLES`) → `components/DraftingPanel.tsx`；`TeamPresenceBar`(234，含仅它使用的 `ActivityEvt`) → `components/TeamPresenceBar.tsx` | ✅ 提交 `9130450`（门禁全绿） |
| 5 | `getCleanPhotoUrl` → `lib/lobbyUtils.ts`（具名导出）；`PoiImage`+`PoiImageProps`(123) → `components/PoiImage.tsx` | ✅ 提交 `9b22dee`（门禁全绿；**顺序已调整**，见下） |
| 6 | `MafengwoStylePanel`(403) → `components/MafengwoStylePanel.tsx`；宿主随之清理 10 个失效 import | ✅ 提交 `6cadefd`（门禁全绿） |
| 7 | `OmniLogo` + `AuthPortalScreen`(273) → `components/`；其余纯函数工具（`shortenRegionName`/`tryExtractJson`/`normalizeLnglat`/`extractStreamingRoutes`）→ `lib/lobbyUtils.ts` | ⬜ 待做（**下一个**） |
| 8 | `UnifiedWorkspace`（1,354 行）按面板继续拆 | ⬜ **未决，需用户确认后再动** |
| 收尾 | 体积/行数对比 + 全门禁复跑 | ⬜ 待做 |

**顺序调整说明**：原计划批 5 是 `MafengwoStylePanel`。依赖分析发现它引用了仍在宿主里的 `PoiImage` 与 `getCleanPhotoUrl`，先抽它会形成 `ContextualLobby ⇄ MafengwoStylePanel` 循环导入，故改为先抽叶子（PoiImage + 工具函数），再抽 `MafengwoStylePanel`。

（行号为拆分前行号，每次抽取后会漂移；以组件名/函数名定位为准。）
**当前进度**：`ContextualLobby.tsx` = **2,619 行 / 141,824 B**（拆分前 3,991 行 / 218,225 B，已减少 34%）；首屏 JS 始终 1,104.5–1,104.6 KB / 13 chunk、CSS 140.2 KB（批 1–6 均无劣化）。已抽出 10 个组件文件 + 1 个工具模块。

**已知待办（不要混进机械搬运批次）**
1. `app/types/index.ts` 已规范定义 `Phase`/`UserProfile`/`RoomMember`（`CommunityPanel`、`ProfileScreen` 即从那里导入），而 `ContextualLobby.tsx` 内还有一份同名重复定义（批 5 中它曾被误卷入新文件，已原样退回）。去重应作为一次独立提交单独处理。
2. `AvatarUploader` 在拆分前（`ebd2d90`）就已是从未使用的 import，属既有死代码；本次刻意保留未动，可另开一次清理提交。

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
- **Playwright**：浏览器在 `work/ms-playwright`（脚本已内置 `PLAYWRIGHT_BROWSERS_PATH`），无需联网下载。
- **内存紧张**（本机实测可用内存约 6–7 GB、提交内存已用 37/64 GB）：跑构建/E2E 时**不要同时**跑 DSH 自身的 `pnpm build`/`dev:web`；必要时 `$env:NODE_OPTIONS='--max-old-space-size=4096'`。
- **崩溃恢复**：因为每批一提交，崩溃最多损失当前一批；`work/split-gate.log` 保留上一次门禁结果。
- 历史背景：2026-09-16 期间 DSH 侧出现过一次 `[ELIFECYCLE] Command failed with exit code 3221226505`（= `0xC0000409`，node 侧硬终止，与项目代码无关），当时正卡在批 1 未提交状态，所以本计划强制「一批准一提交」。
