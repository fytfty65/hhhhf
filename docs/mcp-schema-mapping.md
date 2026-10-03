# MCP Schema 映射契约

`GET /api/v1/planning/providers/tools` 返回的是供应商 `tools/list` 的脱敏审计结果，不返回 token、session、用户数据或原始响应。

每个工具的 `schema_audit` 包含：

- `required`：供应商声明的必填属性名；
- `context_mapped_required`：可由统一 `PlanningContext` 自动提供的字段；
- `default_mapped_required`：由受控、非敏感系统默认值提供的字段（例如城市级查询的 `placeType`）；
- `explicit_request_fields`：必须由用户明确填写的订单/证件字段；
- `unmapped_required`：当前没有安全映射的字段，工具必须保持禁用；
- `invalid_required`：`required` 中不存在于 `properties` 的字段，表示供应商 schema 无效；
- `type_mismatch_required`：名称可以映射但不符合供应商声明的 primitive type/enum，工具必须保持禁用；
- `field_mapping`：每个必填字段的规范化名称、来源类别和状态；
- `status`：`ready`、`requires_request_fields`、`requires_mapping` 或 `invalid_schema`。

响应还包含供应商级 `selection` 摘要：

- `read_only_candidates`：可用于查询的工具名；
- `read_only_candidate`：按供应商/领域优先级选中的查询工具（如果存在）；
- `blocked_mutations`：被规划链路阻断的下单、预订、取消或支付工具；
- `status`：`ready`、`no_read_only_tool`、`not_collected` 或 schema 审计状态。

规划、天气和客流查询只允许使用 `read_only_candidate`。下单与取消只能经订单接口，在用户确认、幂等键、身份/行程授权和字段完整性校验后，单独调用 mutation 工具。

## 映射原则

统一上下文只允许携带规划所需的最小字段：目的地、出发地、日期、人数、预算、币种和偏好。身份证件、联系人、订单号等字段只能来自明确的订单请求，不能从用户画像或模型输出推断。

字段名会在比较前移除下划线、连字符、空格和点号，因此 `check-in`、`check_in`、`checkInDate` 等可以映射到同一日期语义；供应商原始属性名仍按 schema 原样发送。

遇到 `requires_mapping` 或 `invalid_schema` 时，规划链路不会调用该工具；前端只能展示“待供应商字段确认”，并提供官方核验入口。只有 `ready` 的查询工具才能进入供应商候选链路；订单工具还需要显式订单字段和授权的 staging smoke test。

本契约不代表任何供应商当前已经联调成功。真实联调前，应将脱敏后的 `tools/list` 响应保存为测试 fixture，并逐字段确认类型、枚举、日期格式和必填规则。
