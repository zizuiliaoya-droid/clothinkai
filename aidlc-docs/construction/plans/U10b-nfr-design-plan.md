# U10b NFR 设计计划（NFR Design Plan）

> 单元：U10b — 平台商品映射
> 范围：1 增量模式（IntegrityError→409 + upsert 幂等 + 反查）+ 组件清单
> 节奏：NFR Design 阶段 = 本计划 + 2 文档，同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 模式数量
- [Answer] 1 个：**P-U10b-01**（UNIQUE 冲突处理 + create_or_update 幂等 + 引用校验 + find 反查完整伪代码）。

### Q2 — IntegrityError 捕获方式
- [Answer] 与 U06a 导入框架一致：try insert → except IntegrityError（"uq_platform_product_" 匹配）→ 409 PlatformProductConflictError（返回已存在 id）。

### Q3 — create_or_update 路径
- [Answer] SELECT WHERE (tenant_id=ctx, platform, platform_id) → 存在更新 style_id/sku_id/title/is_active；不存在插入。供 U13/U14 内部调用（不暴露 HTTP）。

### Q4 — 文件组织
- [Answer] 为保持 product 模块整洁，新建 `platform_product_models.py` / `platform_product_schemas.py` / `platform_product_service.py` / `platform_product_api.py`（小文件）。主 main.py 注册 platform_product_router。

### Q5 — 权限 scope 落点
- [Answer] 追加到 `modules/product/permissions.py`：SCOPE_PLATFORM_READ / SCOPE_PLATFORM_WRITE。migration 014 seed + 绑 merchandiser(write)/operations(read)（admin 经 * 通配；merchandiser 经 product.*:* 已覆盖）。

---

## 2. 执行步骤

- [x] 2.1 `U10b/nfr-design/nfr-design-patterns.md`：P-U10b-01 完整伪代码（create+IntegrityError / create_or_update SELECT→insert|update / find_by_platform_id / 引用校验）
- [x] 2.2 `U10b/nfr-design/logical-components.md`：新建 4 文件 + permissions.py 追加 + migration 014 + main.py 注册 + 2 测试文件 + 依赖图 + 一致性校验
- [x] 2.3 诊断器无警告 + 与 nfr-requirements / functional-design 一致

---

**等待用户"继续"；本轮直接生成 2 份 NFR 设计文档。**
