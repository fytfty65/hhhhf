# 部署与远程测试手册

> 目标：把"能在我这台机器上跑"变成"朋友用手机/电脑打开就真的能用"，并写清每一步**不满足时会出什么现象**，
> 避免再去猜。全部结论都来自本机实测（2026-10-03/04）。

## 0. 三个进程

| 组件 | 默认地址 | 启动 |
|---|---|---|
| 前端 | `http://<host>:3000` | `cd front && npm run build && npm run start` |
| 网关 | `http://<host>:8080` | `cd gateway && go build -o gateway.exe ./cmd/server && ./gateway.exe` |
| ai-service | `http://127.0.0.1:8000` | `cd ai-service && $env:APP_ENV='production'; .\venv\Scripts\python.exe main.py` |
| Redis | `127.0.0.1:6379` | 事件流 / 会话 / 缓存用；**不启动不会崩，但事件链与多实例会话会降级** |

网关与 ai-service 都必须在各自目录下启动：两者都按相对路径读 `.env` 与 SQLite（`gateway/data/omniroute.db`）。

## 1. 本机开发（现状）

不用配任何环境变量：前端会把 API 指到"当前页面主机 + 8080"。改过端口就设 `NEXT_PUBLIC_GATEWAY_PORT`。

## 2. 远程多人测试（关键步骤）

1. **前端要以生产模式跑**：`npm run build && npm run start`。不要用 `next dev`（慢、且不适合多人）。
2. **网关来源白名单**（`gateway/.env` 的 `ALLOWED_ORIGINS`）必须包含朋友访问的**完整来源**，例如
   `http://192.168.1.5:3000`。不满足的现象：浏览器报 CORS 错，所有接口 403/被拦。
3. **API 基址**：默认不需要配（运行期按访问主机推导）。
   若要显式指定（例如走反向代理或域名），设 `NEXT_PUBLIC_API_BASE=https://tour.example.com`
   —— 注意它是**构建期**注入的，改完必须重新 `npm run build`。
4. **CSP 会自动跟随**：`front/lib/cspTargets.js` 按上面的配置推导网关来源，同时放进
   `connect-src`（http + ws）与 `img-src`（用户实拍图跨源）。
   *不满足的现象与 2026-09-19 瓦片事故一模一样*：DevTools 网络面板里**一条请求都没有**、
   控制台刷 `Refused to connect ... violates ... connect-src`。契约测试 `app/lib/cspTargets.test.ts` 会拦住回退。
5. **HTTPS/WSS**：如果前面挂了 https 反代，前端推导出的 API 基址自动是 https，WebSocket 自动变 **wss**
   （写 ws 会被浏览器直接拦）。网关侧要能被反代到（同机 8080 即可）。
6. **防火墙**：放行 3000 与 8080（或只放反代的 443）。
7. **`APP_ENV=production` 的硬校验**：网关启动时会要求 `METRICS_TOKEN`，
   缺失直接启动失败（这是故意的，避免指标端点裸奔）。本机测试保持 `APP_ENV=development` 即可。

## 3. FCM 推送的网络现实（实测）

| 检查 | 结果 |
|---|---|
| 服务账号 JSON 加载 + 私钥签 JWT | ✅ |
| DNS 解析 `oauth2.googleapis.com` / `fcm.googleapis.com` | ✅ |
| **TCP 443 到这两个域名** | ❌ 均不通（对照：高德/百度 200，约 1s） |
| 换取 Google OAuth token | ❌ 15s 超时 |

结论：**配置本来就是对的，卡在网络**。本机到 Google 的 TCP 443 不通，而浏览器推送还要求
**收件人也能连 Google**。解决办法（已采用）：给**推送这一条链路**挂本地代理。

⚠️ 关键区别：**不要**用全局 `HTTPS_PROXY`。网关同时还在调国内数据源（高德 POI/路况/静态图），
全局代理会把那些请求也绕出国 —— 那是拿"推送能不能通"换"地图能不能用"。
所以代码里用专用变量 `FCM_HTTPS_PROXY`（见 `gateway/internal/push/push.go` 的 `senderHTTPClient`），
只有推送客户端走代理；未配置时行为与从前完全一致（直连）。只支持 http(s) 代理，
socks-only 的客户端请用它的混合/HTTP 端口（Clash 的 7890 就是混合端口）。

实测（2026-10-04）：`FCM_HTTPS_PROXY=http://127.0.0.1:7890` 后投递成功 ——
`{Sent:1 Invalid:[] Failed:0}`，耗时约 2.1s。

**不影响**已经实现的：站内通知中心、WebSocket 实时提醒、风险订阅告警（走房间广播）。

配置项（写在 `gateway/.env`，该文件已被 .gitignore 覆盖，**服务账号路径与私钥永不进仓库**）：
```
FCM_SERVICE_ACCOUNT_JSON_PATH=<服务账号 JSON 的绝对路径>
FCM_PROJECT_ID=<Firebase 项目 id（也可只填 JSON 路径，代码会从 JSON 里读 project_id）>
```
若要用代理（墙内网络基本必须）：再加一行 **`FCM_HTTPS_PROXY=http://127.0.0.1:7890`**
（填你本地代理的 HTTP/混合端口；**不要**用全局 `HTTPS_PROXY`，原因见上）。
注意收件人浏览器也要能连 Google，否则推送到了 FCM 也送不到浏览器。

自检命令（会真的发一条推送）：
```
cd gateway
go run ./cmd/push-smoke -list
go run ./cmd/push-smoke -user <user_id> -title "自检" -body "..."
```

## 4. 其他自检命令

| 目的 | 命令 |
|---|---|
| 协同链路（双账号 + WebSocket + 推演） | `node scripts/smoke-collab.mjs` |
| 用户动作事件链（保存/到访/采纳） | `node scripts/smoke-plan-audit.mjs` |
| 按关联键审计一次规划 | `ai-service/tools/trace_plan.py --recent 3` |
| 事件流真实写入 | `ai-service/tools/probe_event_stream.py` |
| 语义缓存是否真的落 Redis | `ai-service/tools/probe_semantic_cache.py` |
| 前端门禁（tsc/单测/build/E2E） | `powershell -File front/scripts/split-gate.ps1` |
| 两侧测试 | `python -m unittest`（ai-service）/ `go test ./...`（gateway） |

## 5. 常见现象 → 原因

| 现象 | 原因 |
|---|---|
| 页面能开，但所有接口 403 / CORS 报错 | `ALLOWED_ORIGINS` 没有当前的完整来源 |
| 网络面板零请求 + `Refused to connect` | CSP 没放行网关来源（见 2.4） |
| 地图全白、零瓦片请求 | 瓦片域名不在 `connect-src`（MapLibre 用 fetch 取瓦片） |
| `[语义缓存] Redis 写入跳过` | Redis 未就绪；缓存路径本身正常（用探针确认） |
| 网关起不来且提示 METRICS_TOKEN | `APP_ENV=production` 的硬校验（见 2.7） |
| 推送"已发送"但没人收到 | 见第 3 节：网络到 Google 不通 |
| 2C2G 机器越跑越卡 | 不要在机器上 build；`next dev` 与 E2E 别在这台跑；给 Redis 设 `maxmemory` |
