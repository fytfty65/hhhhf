# 官方票价源核查台账（新疆三城）

> 目的：把"**每个官方源到底能不能取到票价**"这件事写成可复查的记录，
> 免得后来的人（或我自己）反复试同一件已经证明做不到的事。
> 每条都给**实测命令**与**实测结论**；结论变了就改这里 + 改 `ai-service/core/official_sites.py` 的登记项。

**口径**：`source="official"` 表示"来自官方域名/官方渠道的页面"；本机读得到具体票价才算**可核实**。
读不到就只提供**官方渠道**（官网/小程序），并且**不猜价**（用户提供的数字只作 `reference_prices` 原样登记）。

| 景区 | 官方域名 | 票价 | 本机能否取到 | 依据（实测） |
|---|---|---|---|---|
| 巴音布鲁克景区 | `www.byblk.cn` | 联票 **140 元**（含门票+区间车） | ✅ **可核实** | 服务端只回 1432 字符壳页；`front/scripts/render-page.mjs` 渲染 `/goumaiyuding/` 后正文写着「票价：140元（包含门票+区间车票）」，≈3.6s |
| 罗布人村寨 | `www.xjlbr.com` | 门票 35 / 观光车 15（**用户提供**） | ❌ 只在微信小程序内 | 站内所有子页渲染后都是同一份 2024 字符壳页；官方公众号文章匿名抓 480 字符、**无头渲染也只 381 字符**（正文不含票价）；`/ykdp`、`/ykdp.html` 均无票价 |
| 巴楚红海景区 | `www.bachu.gov.cn` | **未取到全价票** | ❌ 该源无全价票 | 首页 247 个链接里 **0 个**含"红海"；站内搜索 URL（3 种写法）一律回 19 字符；文旅栏目页无票价行 → 按用户建议**只用于非票价字段**（名称/等级/优惠政策） |

## 复现命令

```powershell
# 渲染单个官方页（front 目录；Playwright 与浏览器在这一侧）
$env:PLAYWRIGHT_BROWSERS_PATH='<repo>\work\ms-playwright'
node front/scripts/render-page.mjs https://www.byblk.cn/goumaiyuding/

# 罗布人村寨：站内各页 + 官方公众号文章
node front/scripts/render-loburen-probe.mjs

# 巴楚红海：首页链接/栏目/站内搜索 找含全价票的页面
node front/scripts/probe-bachu-honghai.mjs
```

## 登记项的字段含义（`ai-service/core/official_sites.py`）

- `price_pages` + `renderable`：需要渲染的票价页（会走无头浏览器，结果落盘缓存 24h）；
- `price_status`：`verified_official_price` / `miniprogram_only` / `no_full_price_found`
  —— 前端会据此显示"为什么没有官方价"（小程序内公布 / 该源只有优惠政策）；
- `reference_prices` + `reference_source`：**用户提供**的数字，原样登记并标明出处，**不冒充**我们抓到的一手价。

## 接新官方源的步骤（照上面三条的实证走）

1. 先 `render-page.mjs` 试**服务端可读**的票价页（大概率是壳页）；
2. 壳页就试**渲染**，看正文有没有「门票/票价 + 数字」（注意闸门：运营时段 `08:30-19:00` 不是价格区间；
   「区间车票」是正经票种，别当模糊词否掉）；
3. 仍取不到就**如实登记** `price_status`，只提供官方渠道 —— 不要为了"看起来有数据"把参考价塞进价格字段；
4. 每个新源都要有单测（`tests/test_official_sites.py`）钉住"能取到/取不到"的结论。
