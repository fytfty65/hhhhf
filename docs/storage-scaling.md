# 存储层容量与演进说明

本文记录 gateway 当前在 SQLite 上的吞吐取舍，以及从单实例 SQLite 演进到
Redis + PostgreSQL 的判据。它描述的是**已经落地**的做法和**尚未落地**的方向，
两者分开标注，避免把规划当成现状。

## 1. 现状：单写者 SQLite

`gateway/internal/database/db.go` 里写池固定 `SetMaxOpenConns(1)`。这是刻意的：

- SQLite 同一时刻只允许一个写事务。让多个连接争抢写锁只会把 `SQLITE_BUSY`
  从"排队"变成"随机报错"，并发度不会提高。
- 单写连接把写入串行化在 Go 侧，错误可观测、可重试，比数据库层随机失败更可控。

代价是吞吐上限固定为单条写事务的速率。下面的四件事都是围绕"减少写锁占用时间
和锁冲突概率"做的，不改变单写模型本身。

### 1.1 读写路径分离

| 路径 | 入口 | 连接池 |
| --- | --- | --- |
| 写 | `database.DB` | 1 个连接 |
| 读 | `database.Reader()` | 最多 4 个连接（WAL 下读不阻塞写） |

规则：

- 所有 `Create` / `Update` / `Delete` / 事务必须走 `database.DB`。
- 纯查询（列表、计数、统计、聚合）应走 `database.Reader()`。
- 不要在 `Reader()` 上做写操作；不要用读池结果去驱动写事务的前提判断。

`Reader()` 带 `readOwner` 守卫：当测试或运行期替换了 `database.DB` 而读池仍指向
旧库时，自动回退到 `DB`，避免读到陈旧数据。

读池设置 `SetMaxIdleConns(0)`、`DisableAutomaticPing`。原因是 Windows 下空闲
连接会持有数据库文件句柄，导致测试的临时目录无法删除；每次读突发起新连接比
泄漏句柄划算。

### 1.2 忙等待与写重试

- DSN 通过 `_busy_timeout=5000` 让每个连接在拿不到写锁时等待 5s，而不是立即报错。
  必须写在 DSN 里，因为 `busy_timeout` 是**每连接**设置，一次性 `PRAGMA` 只影响
  当时那条连接。
- `database/write.go` 提供 `RetryWrite` / `CreateWithRetry` /
  `CreateInBatchesWithRetry`：最多 4 次，退避 25ms / 75ms / 200ms。
- 只对"可重试的写"重试，判定依据是 `sqlite3.ErrBusy` / `ErrLocked`（带字符串兜底）。
  **读不重试**：WAL 读拿到的是快照，重放查询是否安全应由调用方决定。
- 已接入的写入点：规划事件、推荐曝光、反馈、批注、应急、执行、创新指标、
  省份动态、房间消息。新增写入点应复用 `CreateWithRetry`，不要直接 `DB.Create`。

### 1.3 高频事件批量写入

- `POST /api/v1/planning/events/batch` 接受最多 50 条事件，一次
  `CreateInBatchesWithRetry`（批大小 100）。单事件端点保持不变。
- 前端 `app/lib/planningEvents.ts` 用队列攒批：20 条或 600ms 触发一次批量上报；
  批量失败时回退为逐条上报，保证丢批不丢事件。
- 批量对 SQLite 的意义是减少**写锁获取次数**：N 行逐条插入会持有/释放写锁 N 次，
  批量化后变成 N/batchSize 次往返。

### 1.4 复合索引（schema v21）

`database/migrations_indexes.go` 新增 15 个索引，覆盖读路径里"按某个维度过滤 +
按时间排序"的查询：

- 保留期清理：`messages` / `planning_events` / `recommendation_events` /
  `feedback_logs` 的 `created_at`。
- 房间聊天：`messages(room_id, created_at)`。
- 规划分析：`planning_events` 的 `(user_id, created_at)`、`(event_type, created_at)`、
  `(room_id, created_at)`、`(trip_id, created_at)`。
- A/B 指标：`recommendation_events` 的 `(user_id, created_at)`、`(variant, created_at)`。
- 反馈聚合：`feedback_logs` 的 `(room_id, created_at)`、`(user_id, created_at)`。
- 社区：`community_posts(created_at)`、`(dest_city, created_at)`、
  `comments(post_id, created_at)`。

迁移是 `CREATE INDEX IF NOT EXISTS`，非破坏性，无需备份；表不存在时跳过。

## 2. 什么时候必须离开单实例 SQLite

下列任一条件成立，就应启动中期迁移，而不是继续加索引：

- 单次写入 P95 因锁等待超过 200ms，且重试仍在用户可见链路上。
- 写 QPS 持续超过单写事务能力（当前实测上限见压测记录），或事件积压需要
  靠扩大批大小缓解。
- 需要多实例水平扩展：多进程同时写同一个 SQLite 文件违反单写前提。
- 实时协同要求"跨实例广播"，SQLite 没有跨进程通知通道。

## 3. 中期目标架构（尚未落地）

| 数据 | 归属 | 理由 |
| --- | --- | --- |
| 实时事件、房间在线状态、临时协同状态（投票草稿、正在输入、锁） | Redis | 高频、可丢、带 TTL，Pub/Sub 天然支持跨实例广播 |
| 用户、行程、规划结果、社区、反馈等持久化业务数据 | PostgreSQL | 多写并发、事务隔离、外键与索引能力 |
| 本地开发、单实例小规模部署 | SQLite | 零运维、单文件、备份简单，继续作为默认开发方案 |

切换时应保留的统一抽象：

- 仓储接口按业务域拆分（房间、消息、规划、执行、社区、反馈），不把 GORM 模型
  泄漏到 handler。
- 事件表保留 append-only 语义，未来可直接作为 CDC 源同步到 PostgreSQL。
- 读路径继续走 `Reader()` 语义；换成 PostgreSQL 后它指向只读副本。

迁移顺序建议：先 Redis 承接房间实时事件与在线状态（收益最大、风险最低），
再把写入最重的规划事件与反馈迁到 PostgreSQL，最后把社区与用户数据迁走。
SQLite 作为开发默认实现保留，不是被删除的路径。

## 4. 新增代码的检查清单

- 写操作是否走了 `DB` 且使用 `CreateWithRetry`？
- 查询是否走了 `Reader()`？
- 新事件是否可以先攒批，再调用批量端点？
- 新查询是否有 `(过滤维度, created_at)` 复合索引，或能复用已有索引？
- 是否引入了"读后写"的跨请求依赖？在单写模型下这会放大锁竞争。
