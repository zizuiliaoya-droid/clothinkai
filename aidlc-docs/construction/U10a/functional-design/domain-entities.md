# U10a 领域实体（设计制版全流程）

> 单元：U10a — 设计制版全流程（EP03-S02~S14）
> 依赖：U02（Style/Sku，复用并扩展 design_status）、U07（notification 表 + NotificationService）
> 特征：新建 modules/design；3 业务子表 + 1 历史表；design_status 7 态 Enum 扩展（DB 字段不变）

---

## 1. 实体总览

| 实体 | 类型 | 来源 |
|---|---|---|
| Style.design_status | 字段（VARCHAR(16)） | U02 既有；U10a 扩展 Enum 取值 2→7 |
| DesignStatus | Python Enum | U10a 新建（modules/design/enums.py） |
| StyleFabric | ORM（1:1 style，TenantScopedModel） | U10a 新建 style_fabric |
| StylePattern | ORM（1:1 style） | U10a 新建 style_pattern |
| StyleCraft | ORM（1:1 style） | U10a 新建 style_craft |
| DesignWorkflowLog | ORM（N:1 style，append-only） | U10a 新建 design_workflow_log |
| Notification | ORM（已存在） | U07；U10a 复用承载设计推进通知 |
| Sku.cost_price / tag_price | 字段（已存在） | U02；U10a 自动核价/吊牌价写入 |

> U10a 不改 style/sku 表结构（design_status 已是 VARCHAR(16)）；新增 3 子表 + 1 历史表（migration 013）。

---

## 2. DesignStatus 枚举（7 态）

```python
class DesignStatus(str, Enum):
    DESIGNING = "设计中"      # 初始（create_design）
    PATTERNING = "制版中"     # submit_fabric 后
    CRAFTING = "工艺录入"     # submit_grading 后
    COMPLETING = "待补全"     # submit_craft 后
    PRICING = "待核价"        # submit_costing 后
    MASS_PRODUCTION = "大货"  # confirm_price 后（终态）
    CANCELLED = "已取消"      # cancel（终态，不可逆）
```

- U02 既有取值 `大货` / `设计中` 兼容（同字符串）；其余 5 态新增。
- 终态：`大货`、`已取消`（不可再推进/驳回）。

---

## 3. StyleFabric（面辅料，style_fabric）

| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id / created_at / updated_at | TenantScopedModel | — |
| style_id | UUID FK(style) UNIQUE | 1:1 |
| fabrics | JSONB | 面料列表 [{name, composition, usage, ...}] |
| accessories | JSONB | 辅料列表 |
| is_completed | bool | 设计助理补齐标记（S08） |
| remark | Text? | — |

> S03 设计师初填 fabrics/accessories；S08 设计助理补齐（upsert 同行，is_completed=true）。

---

## 4. StylePattern（版型，style_pattern）

| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id / ts | TenantScopedModel | — |
| style_id | UUID FK UNIQUE | 1:1 |
| pattern_no | VARCHAR(64) | 版号（S04 必填） |
| pattern_file_key | VARCHAR(256)? | R2 private 桶 key（制版文件） |
| grading_data | JSONB? | 放码数据（S05） |

---

## 5. StyleCraft（工艺，style_craft）

| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id / ts | TenantScopedModel | — |
| style_id | UUID FK UNIQUE | 1:1 |
| craft_info | JSONB | 工艺信息（S07，跟单录入） |

---

## 6. DesignWorkflowLog（状态变迁历史，design_workflow_log）

| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id / created_at | TenantScopedModel（无 updated，append-only 语义） | — |
| style_id | UUID FK | — |
| from_status / to_status | VARCHAR(16) | 转移前后 |
| action | VARCHAR(32) | submit_fabric / submit_grading / reject / cancel ... |
| driven_by | VARCHAR(32)? | 角色口径（version_maker / merchandiser / design_assistant / admin） |
| actor_id | UUID? | 操作人 |
| reason | Text? | 驳回/取消原因 |

> 与 audit_log 并存：design_workflow_log 是业务可见的流程时间线（前端展示）；audit_log 是安全审计。

---

## 7. NotificationType 扩展（U07 枚举追加）

```python
# modules/wecom/enums.py NotificationType 追加
DESIGN_ADVANCE = "design_advance"   # 推进到下一环节通知下一角色
DESIGN_REJECT  = "design_reject"    # 驳回通知上游
DESIGN_DONE    = "design_done"      # 转大货通知设计师
```

---

## 8. ER 图

```mermaid
erDiagram
    STYLE ||--o| STYLE_FABRIC : has
    STYLE ||--o| STYLE_PATTERN : has
    STYLE ||--o| STYLE_CRAFT : has
    STYLE ||--o{ DESIGN_WORKFLOW_LOG : tracks
    STYLE ||--o{ SKU : contains
    STYLE {
        uuid id
        string design_status
    }
    SKU {
        decimal cost_price
        decimal tag_price
    }
```

---

## 9. 演化
- U13 采集/U14 报表与本单元无直接耦合。
- design_workflow_log 未来可支撑环节耗时统计报表。
