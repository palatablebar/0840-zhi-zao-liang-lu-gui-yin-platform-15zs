# 字段定义

| 字段 | 类型 | 说明 |
|---|---|---|
| `row_id` | text | 主键 |
| `work_order` | text | 业务字段 |
| `stage_seq` | int_positive | 业务字段 |
| `stage` | text | 业务字段 |
| `input_qty` | int_positive | 业务字段 |
| `good_qty` | int | 业务字段 |
| `scrap_qty` | int | 业务字段 |
| `rework_qty` | int | 业务字段 |
| `rework_good` | int | 业务字段 |

空字符串、`-`、`NA`、`null` 和 `未提供` 按空值处理。金额字段在整理层转为整数分或整数最小单位；日期按 ISO 日期解析；带时间的字段按本地时间解析。标识字段保持为文本，前导零不会被去除。

## 交接量

下道 `input_qty` 应等于上道 `good_qty + rework_good`。不相等时该工单在补报中沿用已签发结果；相等时才用新数据更新损耗。

## 补报字段

补报是同一套字段的子集，至少包含 `row_id` 或 `(work_order, stage_seq)` 用于定位：

| 字段 | 说明 |
|---|---|
| `row_id` | 优先按主键匹配 |
| `work_order` + `stage_seq` | 主键缺失时的匹配键，也是晚到下道实收行的定位键 |
| 其余数量字段 | 只覆盖补报中出现的列，未出现的列保持不变 |

