# 供应商字段映射契约 v1

该契约描述 Gateway 如何把不同供应商的只读响应映射为统一候选结构。它不是任何供应商的官方 schema，也不代表平台拥有出票、支付或预订权限。

## 统一字段

| 统一字段 | 兼容输入示例 | 说明 |
| --- | --- | --- |
| `id` | `id`, `hotelId`, `poi_id`, `offerId`, `flight_id` | 供应商候选标识 |
| `name` | `name`, `title`, `poi_name`, `hotelName`, `restaurantName` | 展示名称 |
| `price` | `price`, `display_price`, `lowestPrice`, `total_price`, `fare` | 只从供应商响应解析，不做估算 |
| `duration_minutes` | `duration_minutes`, `durationMin`, `travelTimeMinutes`, `duration_seconds` | 秒会转换为分钟，并保留来源 |
| `observed_at` | `observed_at`, `updatedTime`, `collected_at`, `data_time` | 缺失时标记 `observed_at_provided=false` |
| `opening_hours` | `opening_hours`, `open_hours`, `open_time` | 不补写营业时间 |
| `crowd_level` | `crowd_level`, `crowdLevelText` | 进入枚举校验 |
| `condition` | `condition`, `weatherType`, `weather_desc` | 进入枚举校验 |
| `booking_url` | `booking_url`, `bookingLink`, `deepLink` | 仅允许安全的 HTTP(S) 链接，并删除凭据查询参数 |

## 枚举校验

目前对影响实时决策的字段记录 `extra.enum_validation`：

- `availability`: `available`、`unavailable`、`limited`、`unknown`
- `crowd_level`: `low`、`medium`、`high`、`very_high`、`unknown`
- `condition`: `sunny`、`cloudy`、`rain`、`snow`、`storm`、`fog`、`unknown`

供应商值会映射为规范值。无法识别的值不会被删除，而是标记为 `unrecognized`，并不能通过依赖该字段的实时事实门禁。

## Fixture 规则

`gateway/internal/handlers/testdata/providers/` 中的 fixture 必须：

1. 声明 `contract_version`；
2. 声明 `source: redacted_fixture`；
3. 不包含 token、session、用户标识、真实订单号或私有 URL；
4. 只用于映射和门禁测试，不代表真实供应商已经联调成功。

当前脱敏样例：[planning_aliases_v1.json](../gateway/internal/handlers/testdata/providers/planning_aliases_v1.json)。
