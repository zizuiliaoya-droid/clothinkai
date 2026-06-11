# U10a 代码生成计划（Code Generation Plan）

> 单元：U10a — 设计制版全流程
> 分批：4 批 + Build & Test；modules/design 全套 + migration 013 + 横切 3 改动
> Build & Test：Docker PG16:5552 + Redis7:6407 + Py3.12

---

## 0. 关键修订（基于现有代码调查）

- **Sku 无 tag_price 字段**（U02 仅 cost_price/purchase_price/base_price/sourcing_type）。故事 S10 需 `sku.tag_price` → migration 013 追加 `ALTER TABLE sku ADD COLUMN tag_price NUMERIC(10,2)`（nullable，无回填，附 CheckConstraint ≥0）+ Sku 模型加 tag_price 字段。基础设施增量在 infra-design 4 表基础上 +1 列（additive，零风险）。

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — Sku.tag_price
- [Answer] migration 013 ALTER sku ADD tag_price NUMERIC(10,2) NULL + ck ≥0；Sku 模型加字段；SkuResponse 可后续暴露（本单元不强制改 U02 schema）。set_tag_price 写所有 active SKU。

### Q2 — create_design 与 Style 创建
- [Answer] DesignService.create_design 直接用 StyleRepository 建 Style（design_status="设计中"，main_image_key→R2 public）+ 唯一性校验，不强依赖 StyleService（避免其默认 design_status="大货"）。

### Q3 — 文件上传方式
- [Answer] 本单元 API 接收 main_image_key / pattern_file_key（前端已通过 attachment 预上传拿到 key），design 仅存 key；不在 design 端点直接处理 multipart（与 U02 一致：style 存 key）。

### Q4 — available_actions
- [Answer] domain.compute_available_actions(design_status, role_codes) 用 DesignStateMachine.get_valid_actions + admin 补 cancel；detail 响应返回。

### Q5 — 通知角色解析
- [Answer] RoleRepository.list_user_ids_by_role_code(code)：join role+user_role，租户内（测试 bypass 显式 tenant 过滤——但 user_role 已 tenant scoped，沿用 list_codes_for_user 风格不加显式 tenant 即可，RLS/钩子处理；为确定性测试加 WHERE 注释）。

### Q6 — migration 013 内容
- [Answer] 4 表 + RLS + UNIQUE(style_id)×3 + FK CASCADE + design_workflow_log idx + ALTER sku ADD tag_price + design.* scope seed 绑角色（幂等）。接 012。

### Q7 — 测试端口
- [Answer] Docker PG16:5552 + Redis7:6407。

### Q8 — 不改 U09/U02 既有
- [Answer] 自动核价绕过 SkuService 直接 repository.bulk_update_sku_cost_price；不改 product/service.py；不改 U09 字段权限。

---

## 2. 批次步骤

### Batch 1 — 模块基础 + 模型 + Schema
- [x] 1.1 modules/design/{__init__,enums,permissions,exceptions}.py
- [x] 1.2 modules/design/models.py（StyleFabric/StylePattern/StyleCraft/DesignWorkflowLog）
- [x] 1.3 product/models.py：Sku +tag_price 字段（+ck）
- [x] 1.4 modules/design/schemas.py（提交 Schema + 响应）

### Batch 2 — 状态机 + domain + repository
- [x] 2.1 modules/design/state_machines.py（DESIGN_TRANSITIONS + DesignStateMachine + REJECT_PREVIOUS/DRIVEN_BY/NOTIFY_ROLE）
- [x] 2.2 modules/design/domain.py（核价求和 + compute_available_actions）
- [x] 2.3 modules/design/repository.py（upsert 子表 + update_design_status 乐观并发 + add_workflow_log + bulk SKU cost/tag + list_grouped + get_detail）

### Batch 3 — service + api + 横切
- [x] 3.1 modules/design/service.py（DesignService 13 方法）
- [x] 3.2 modules/design/{deps,api}.py
- [x] 3.3 wecom/enums.py +DESIGN_*；auth/repository.py +list_user_ids_by_role_code；main.py 注册 design_router

### Batch 4 — migration 013 + 测试
- [x] 4.1 alembic/versions/013_u10a_create_design_tables.py（4 表 + RLS + ALTER sku tag_price + scope seed）
- [x] 4.2 tests/unit/test_design_state_machine.py + test_design_costing.py
- [x] 4.3 tests/integration/test_design_workflow.py + test_design_notification.py
- [x] 4.4 tests/api/test_design_api.py

### Build & Test
- [ ] B.1 Docker PG16:5552 + Redis7:6407；alembic upgrade head（含 013）；U10a 子集 + 全量回归；覆盖率 ≥70%

---

**本轮执行 Batch 1；后续"继续"逐批推进至 Build & Test。**
