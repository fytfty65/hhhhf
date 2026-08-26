# 三大可视化模块功能评估与优化方案报告

> 报告日期：2026-08-25
> 范围：3D 态势雷达、博弈裁决看板、3D 风控雷达 三模块评估优化，及两个具体功能缺陷修复

---

## 一、总体诊断（"异常鸡肋"的根源）

三个模块表面视觉炫酷，但**展示的是硬编码/伪造数据，与用户的具体行程无关**，且**缺少可操作的交互闭环**，导致用户看完后既得不到真实信息、也无法据此决策。核心矛盾：

- 数据层：`CII` 指数由 `(16.8 + cityName.length*4)%15` 伪造、裁决文本固定写"预算偏离红线"、气象/治安文案写死。
- 交互层：3D 节点不可点击、裁决看板点击只选中第一个节点、风控预警无法定位到受影响节点。
- 结果：模块沦为"装饰性大屏"，而非"决策辅助工具"。

---

## 二、问题清单

| 编号 | 模块 | 严重度 | 问题描述 |
| --- | --- | --- | --- |
| P1 | 3D 态势雷达 | 高 | 节点无交互，无法点击聚焦到具体地点 |
| P2 | 3D 态势雷达 | 高 | CII 指数用伪随机公式兜底，误导用户 |
| P3 | 3D 态势雷达 | 中 | 仅展示"打卡点N"通用文案，未呈现节点真实成本/评分/拥挤度 |
| P4 | 博弈裁决看板 | 高 | "检测到预算偏离红线"为硬编码话术（假警报） |
| P5 | 博弈裁决看板 | 高 | "高溢价节点"固定显示"核心商圈高溢价点"，非真实节点 |
| P6 | 博弈裁决看板 | 高 | 平替推荐取的是第一个酒店节点，非预算优化结果 |
| P7 | 博弈裁决看板 | 中 | 交互与展示脱节（点击仅选中第 0 个节点） |
| P8 | 3D 风控雷达 | 高 | CII 伪造兜底 |
| P9 | 3D 风控雷达 | 高 | "治安评分良好/气象提示"为写死文案 |
| P10 | 3D 风控雷达 | 中 | 预警未与具体节点/坐标联动 |

**具体功能缺陷：**

| 缺陷 | 根因 |
| --- | --- |
| A. 同商圈备选酒店"详情"按钮无响应 | `hotel_candidates` 由 LLM 生成为 `{name, price}` 对象，**不含 `amap_url`**，前端 `cand.amap_url` 为 `undefined`，导致 `href=undefined` 点击无效 |
| B. 规划地点图片无法加载 | ①部分 POI 无用户实拍照片；②图片 URL 未统一归一化（`http://` 混合内容被拦截、`//` 协议相对 URL 被前端过滤）；③未调用 `place/detail` 按 POI ID 补图 |

---

## 三、优化方案与实施步骤（本期已完成）

### 1. 修复缺陷 A：酒店详情按钮
- 位置：[page.tsx](file:///d:/OminRoute/front/app/page.tsx#L2784-L2808)
- 方案：`candUrl` 在对象无 `amap_url` 时回退到 `https://www.amap.com/search?query=${encodeURIComponent(名称)}`；并新增 `openHotelDetail` 用 `window.open` 兜底，`stopPropagation` 隔离父级卡片点击。

### 2. 修复缺陷 B：图片加载
- 位置：[agent.py](file:///d:/OminRoute/ai-service/api/agent.py#L566-L578)、[agent.py](file:///d:/OminRoute/ai-service/api/agent.py#L610-L646)
- 方案：
  1. **经纬度最近邻匹配**（`_find_match_by_coord`，haversine 距离，1.5km 阈值）替代名称模糊匹配，把路线节点精确绑定到正确 POI 的真实照片。
  2. **`place/detail` 按 POI ID 批量补图**：对无实拍照片的 POI 并发调用 `place/detail` 补齐真实照片（上限 12 个/次）。
  3. **图片 URL 归一化**：`http://`→`https://`，`//`→`https://`，杜绝混合内容拦截与协议相对过滤。

### 3. 优化博弈裁决看板（真实数据驱动）
- 位置：[FullRouteVisualizer.tsx](file:///d:/OminRoute/front/app/FullRouteVisualizer.tsx#L227-L238)、[FullRouteVisualizer.tsx](file:///d:/OminRoute/front/app/FullRouteVisualizer.tsx#L325-L369)
- 方案：新增 `parseCostNumber` 解析节点成本；计算当日真实估算消费、最高成本节点、最低成本节点，并与 `budgetData.daily_avg` 对比，**真实呈现是否超预算**；点击"最高成本节点/更低成本平替"分别聚焦到真实节点。

### 4. 优化 3D 态势雷达与风控雷达（真实化 + 交互）
- 位置：[FullRouteVisualizer.tsx](file:///d:/OminRoute/front/app/FullRouteVisualizer.tsx#L172-L174)、[FullRouteVisualizer.tsx](file:///d:/OminRoute/front/app/FullRouteVisualizer.tsx#L457-L484)、[FullRouteVisualizer.tsx](file:///d:/OminRoute/front/app/FullRouteVisualizer.tsx#L412-L440)
- 方案：
  1. CII 无真实数据时显示"待评估"，不再伪造数值。
  2. 风控简报无预警时显示诚实的"待评估"文案，删除写死的气象/治安文案。
  3. 3D 节点增加点击交互：点击节点 → 聚焦 2D 地图对应地点并切回 MICRO 视图。

---

## 四、测试验证方法

| 项 | 方法 | 结果 |
| --- | --- | --- |
| 后端语法 | `python -m py_compile api/agent.py` | ✅ 通过 |
| 前端类型 | `npx tsc --noEmit` | ✅ 通过 |
| 后端单测 | `python -m unittest tests.test_innovations -v` | ✅ 9/9 |
| 网关单测 | `go test ./internal/service/...` | ✅ ok |
| 缺陷 A 验证 | 检查 `hotel_candidates` 对象含 name/price 无 amap_url，点击后 `window.open` 正常打开高德搜索页 | 待运行时复测 |
| 缺陷 B 验证 | 发起真实规划请求，检查 WebSocket `final_route` 中各节点 `photos` 数组是否来自高德（非 Unsplash），无照片节点是否回落静态地图/占位 | 待运行时复测 |
| 三模块验证 | 有预算数据时看板显示真实金额；无 CII 时显示"待评估"；点击 3D 节点能聚焦 2D 节点 | 待运行时复测 |

---

## 五、资源需求

- **无新增依赖**：后端复用 `httpx`/`asyncio`/`math`，前端复用现有 React/framer-motion/three-globe。
- **外部服务**：高德 `place/text`、`place/detail` 配额（`place/detail` 每次最多并发 12 请求，仅在无照片 POI 上触发，量级可控）。
- **测试资源**：一个可联通的测试城市行程用例，用于验证 `photos` 回填与详情跳转。

---

## 六、时间规划（分阶段，按优先级排序）

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 阶段 1 | 两大缺陷修复 + 三模块真实数据化与基础交互（CII 真实化、裁决看板真实成本、节点点击聚焦） | ✅ 本期完成 |
| 阶段 2 | 实时数据源深度接入：天气/拥挤度（`estimate_crowdedness`）按节点注入态势雷达与风控简报 | 建议下一迭代 |
| 阶段 3 | 交互深化：预警点击定位到受影响节点、预算对比图表、3D 节点按成本/评分差异化着色 | 建议中长期 |

---

## 七、上线计划

1. **回归验证**：`py_compile` + `tsc` + `go test` + Python 单测全绿（已完成）。
2. **联调自测**：以真实城市发起规划，逐条复核缺陷 A/B 与三模块新文案（待执行）。
3. **灰度发布**：先部署后端 `agent.py` 图片增强 + 前端改动，观察图片加载成功率（`photos` 非空占比）与详情跳转。
4. **监控指标**：图片非空率、详情点击打开率、裁决看板"预算健康/超预算"判断准确率、CII"待评估"出现频率（用于判断安全数据源接入优先级）。