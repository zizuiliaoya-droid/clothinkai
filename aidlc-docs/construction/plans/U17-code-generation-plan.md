# U17 代码生成计划（Code Generation Plan）

> 单元：U17 — 套装 + BI 看板 + 报表导出（EP02-S08、EP09-S06、EP09-S08）（V2 收官单元）
> 分批：**2 批** + Build & Test
> Build & Test：Docker PG16:5560 + Redis7:6415 + Py3.12

---

## 0. 澄清回答（预填 [Answer]）

- [Answer] 套装落 modules/product（bundle_models/schemas/repository/service/api）；BI/导出落 modules/report（user_preference_models/service + bi_service + export_service + bi_api + export_api）。
- [Answer] migration 021：bundle_product + bundle_item + user_preference 3 表 + product.bundle/report.export scope seed。
- [Answer] EP02-S08 BundleService split_quantities；EP09-S06 BiService 复用 report service + 布局 user_preference；EP09-S08 ReportExportService openpyxl 流式 + report.export:read 403。

---

## 1. 步骤（2 批）

### Batch 1 — 模型 + Schema + 权限 + 异常 + 指标 + repository
- [x] 1.1 product/bundle_models.py（BundleProduct + BundleItem）+ report/user_preference_models.py
- [x] 1.2 product/bundle_schemas.py（4 schema）
- [x] 1.3 product/permissions +bundle scope / report/advanced_permissions +export / report/exceptions +ReportExportTypeInvalidError
- [x] 1.4 core/metrics +report_export_total
- [x] 1.5 product/bundle_repository.py（BundleRepository）

### Batch 2 — Service + API + Deps + main + migration + conftest + 测试
- [x] 2.1 product/bundle_service.py（create + get_with_items + split_quantities）+ bundle_api.py
- [x] 2.2 report/user_preference_service.py + bi_service.py（get_dashboard + DEFAULT_BI_LAYOUT）+ export_service.py（openpyxl 流式）
- [x] 2.3 report/bi_api.py（bi + layout）+ export_api.py（{type}/export）
- [x] 2.4 product/deps +BundleServiceDep / report/deps +Bi/Export/UserPreference ServiceDep
- [x] 2.5 main.py 挂 3 router + migration 021 + conftest import
- [x] 2.6 测试 3 文件（unit/integration/api）

### Build & Test
- [x] B.1 Docker PG16:5560 + Redis7:6415；alembic upgrade head（含 021）；U17 子集 + 全量回归；覆盖率 ≥70%

---

**本轮执行全部 2 批 + Build & Test。**
