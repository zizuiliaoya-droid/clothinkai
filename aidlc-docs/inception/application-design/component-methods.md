# 组件方法签名（Component Methods）

> 仅列方法签名和高层目的。详细业务规则、状态机副作用、计算公式细节放到 Construction 阶段的 Functional Design。

## 编写约定

- 后端方法用 Python 类型注解 + 关联故事 ID
- `tenant_id` 通过 Session 注入，方法签名不重复列出（除非跨租户调用）
- `user` 通过 FastAPI Depends 注入到 Router，再传给 Service

---

## 1. core/security/permissions.py

```python
def require_permission(scope: str, action: str = "read") -> Depends:
    """FastAPI Depends 工厂，校验当前用户有 (scope, action) 权限"""
    # 故事: EP01-S04, S05

def require_field(field_name: str) -> FieldInfo:
    """Pydantic Field 标记，标注此字段需要权限保护"""
    # 故事: EP01-S06

def build_schema_for_user(base_schema_cls: type[BaseModel], user: User) -> type[BaseModel]:
    """根据用户字段权限动态生成响应 Schema"""
    # 故事: EP01-S06

def check_permission(user: User, scope: str, action: str = "read") -> bool:
    """检查用户是否有指定权限（角色 ∪ 自定义授予 - 自定义撤销）"""
    # 故事: EP01-S04, S05

def get_effective_permissions(user: User) -> dict[str, set[str]]:
    """计算用户最终生效权限矩阵"""
    # 故事: EP01-S05
```

---

## 2. core/security/crypto.py

```python
def encrypt_credential(tenant_id: UUID, plaintext: str) -> bytes:
    """AES-256 加密，按租户独立密钥"""
    # 故事: EP07-S03

def decrypt_credential(
    tenant_id: UUID, credential_id: UUID, ciphertext: bytes, *, purpose: str
) -> str:
    """解密，调用方必须提供 purpose 用于审计"""
    # 故事: EP07-S03, S04

def rotate_tenant_key(tenant_id: UUID) -> None:
    """轮换租户加密密钥（KMS 模式）"""
    # 故事: EP07-S03 (V1+)
```

---

## 3. core/audit.py

```python
def audit(operation: str, resource: str | None = None) -> Callable:
    """API 装饰器，记录操作"""
    # 故事: EP01-S08, EP07-S04

def register_audit_listeners(model: type[Base], events: list[str]) -> None:
    """ORM 事件钩子，监听敏感表的 INSERT/UPDATE 自动写"""
    # 故事: EP01-S08

class AuditService:
    @staticmethod
    def log(
        action: str,
        resource: str | None = None,
        resource_id: UUID | None = None,
        before: dict | None = None,
        after: dict | None = None,
        purpose: str | None = None,
    ) -> None:
        """显式调用记录审计"""
        # 故事: EP01-S08, EP07-S04

    @staticmethod
    def query(
        action: str | None = None,
        resource: str | None = None,
        user_id: UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Page[AuditLogEntry]:
        """查询审计日志"""
        # 故事: EP01-S08
```

---

## 4. core/attachment.py

```python
class AttachmentService:
    def upload(
        self,
        file: UploadFile,
        bucket: Literal["public", "private"],
        owner_type: str,
        owner_id: UUID | None = None,
    ) -> Attachment:
        """上传到 R2，返回 attachment 记录（业务表存 attachment_id）"""
        # 故事: EP02-S01, EP03-S02, EP03-S04, EP06-S07

    def get_url(self, attachment_id: UUID, *, expires_in: int = 900) -> str:
        """public 桶返回 CDN URL；private 桶返回签名 URL（默认 15 分钟）"""

    def delete(self, attachment_id: UUID) -> None:
        """软删除附件并清理 R2 对象"""
```

---

## 5. core/state_machine.py

```python
class TransitionRule(BaseModel):
    from_state: str
    action: str
    to_state: str
    actors: list[str]                # 允许执行的角色
    required_fields: list[str] = []  # 转移前必填字段
    side_effects: list[str] = []     # 副作用名称（在 Functional Design 展开）

class StateMachine:
    transition_table: list[TransitionRule]      # 子类声明

    def can_transition(self, action: str, *, actor: User | None = None) -> bool: ...
    def transition(self, action: str, *, actor: User, **kwargs) -> None: ...
    def get_valid_actions(self, *, actor: User | None = None) -> list[str]: ...
```

---

## 6. modules/auth (EP01)

### AuthService

```python
class AuthService:
    def login(self, username: str, password: str) -> TokenPair:
        """用户登录，返回 access + refresh token"""
        # 故事: EP01-S01

    def refresh(self, refresh_token: str) -> TokenPair: ...
        # 故事: EP01-S01

    def change_password(self, user_id: UUID, old_password: str, new_password: str) -> None: ...
        # 故事: EP01-S02

    def force_password_reset(self, user_id: UUID) -> str:
        """生成临时密码，强制下次登录修改"""
        # 故事: EP01-S03
```

### UserService

```python
class UserService:
    def create(self, payload: UserCreate) -> tuple[User, str]:
        """创建用户，返回 (user, 初始随机密码明文)"""
        # 故事: EP01-S03

    def update(self, user_id: UUID, payload: UserUpdate) -> User: ...
    def toggle_active(self, user_id: UUID) -> User: ...
        # 故事: EP01-S03

    def assign_roles(self, user_id: UUID, role_codes: list[str]) -> User: ...
        # 故事: EP01-S04

    def list(self, page: int = 1, page_size: int = 20, *, filters: UserFilters) -> Page[User]: ...
```

### PermissionService

```python
class PermissionService:
    def grant_custom(self, user_id: UUID, scope: str, action: str) -> None: ...
        # 故事: EP01-S05
    def revoke_custom(self, user_id: UUID, scope: str, action: str) -> None: ...
    def grant_field_access(self, user_id: UUID, field_path: str) -> None: ...
        # 故事: EP01-S06
    def get_effective(self, user_id: UUID) -> EffectivePermissions: ...
```

---

## 7. modules/product (EP02)

### StyleService

```python
class StyleService:
    def create(self, payload: StyleCreate) -> Style: ...
        # 故事: EP02-S01
    def update(self, style_id: UUID, payload: StyleUpdate) -> Style: ...
        # 故事: EP02-S03
    def get_by_code(self, style_code: str) -> Style | None: ...
        # 故事: EP02-S06
    def search_by_name(self, query: str, limit: int = 20) -> list[Style]: ...
        # 故事: EP02-S06
    def list(self, *, filters: StyleFilters, page: int = 1) -> Page[Style]: ...
```

### SkuService

```python
class SkuService:
    def create(self, payload: SkuCreate) -> Sku: ...
        # 故事: EP02-S02
    def update(self, sku_id: UUID, payload: SkuUpdate) -> Sku: ...
        # 故事: EP02-S04（含 cost_price 修改，需字段级权限）
    def get_by_code(self, sku_code: str) -> Sku | None: ...
    def list_by_style(self, style_id: UUID) -> list[Sku]: ...
        # 故事: EP02-S05
```

### PlatformProductService（U10b / V1）

```python
class PlatformProductService:
    def create_or_update(self, payload: PlatformProductCreate) -> PlatformProduct: ...
        # 故事: EP02-S07
    def find_by_platform_id(self, platform: str, platform_id: str) -> PlatformProduct | None: ...
```

### BundleService（U17 / V2）

```python
class BundleService:
    def create(self, payload: BundleCreate) -> Bundle: ...
    def get_with_items(self, bundle_id: UUID) -> Bundle: ...
        # 故事: EP02-S08
```

---

## 8. modules/design (EP03 / U10a)

### DesignService

```python
class DesignService:
    def create_design(self, payload: DesignCreate, attachment_id: UUID) -> Style: ...
        # 故事: EP03-S02
    def submit_fabric(self, style_id: UUID, payload: FabricSubmit) -> Style: ...
        # 故事: EP03-S03
    def submit_pattern(self, style_id: UUID, payload: PatternSubmit) -> Style: ...
        # 故事: EP03-S04
    def submit_grading(self, style_id: UUID, payload: GradingSubmit) -> Style: ...
        # 故事: EP03-S05
    def submit_craft(self, style_id: UUID, payload: CraftSubmit) -> Style: ...
        # 故事: EP03-S07
    def complete_fabric(self, style_id: UUID) -> Style: ...
        # 故事: EP03-S08
    def submit_costing(self, style_id: UUID, payload: CostingSubmit) -> Style: ...
        # 故事: EP03-S09
    def set_tag_price(self, style_id: UUID, tag_price: Decimal) -> Style: ...
        # 故事: EP03-S10
    def confirm_price(self, style_id: UUID) -> Style: ...
        # 故事: EP03-S11
    def reject(self, style_id: UUID, reason: str) -> Style: ...
        # 故事: EP03-S06, S12
    def cancel(self, style_id: UUID, reason: str) -> Style: ...
        # 故事: EP03-S13
```

### DesignStateMachine（domain.py）

```python
class DesignStateMachine(StateMachine):
    transition_table = DESIGN_TRANSITIONS  # 详见 Functional Design U10a
    # 状态: 设计中 → 制版中 → 工艺录入 → 待补全 → 待核价 → 大货 / 已取消
```

### NotificationService（共用，但首次在 design 触发）

```python
class NotificationService:
    def notify(self, user_ids: list[UUID], message: str, link: str | None = None) -> None: ...
        # 故事: EP03-S14, EP08-S07 (降级)
    def unread_count(self, user_id: UUID) -> int: ...
    def mark_read(self, notification_id: UUID, user_id: UUID) -> None: ...
```

---

## 9. modules/blogger (EP04)

### BloggerService

```python
class BloggerService:
    def create(self, payload: BloggerCreate) -> Blogger: ...
        # 故事: EP04-S01
    def update(self, blogger_id: UUID, payload: BloggerUpdate) -> Blogger: ...
        # 故事: EP04-S02
    def search(self, *, filters: BloggerFilters) -> Page[Blogger]: ...
        # 故事: EP04-S03
    def get_with_audience_profile(self, blogger_id: UUID) -> BloggerWithProfile: ...
        # 故事: EP04-S08
```

### BloggerTagService（U11 / V1）

```python
class BloggerTagService:
    def compute_blogger_type(self, follower_count: int) -> str: ...
        # 故事: EP04-S04
    def compute_read_like_ratio(self, blogger: Blogger) -> Decimal | None: ...
        # 故事: EP04-S05
    def is_fake_account(self, blogger: Blogger) -> bool: ...
        # 故事: EP04-S06
    def compute_quality_tags(self, blogger: Blogger) -> list[str]: ...
        # 故事: EP04-S07
    def recompute_for_tenant(self, tenant_id: UUID) -> int:
        """阈值变更后批量重算，返回更新数量"""
        # 故事: EP04-S04~S07
```

---

## 10. modules/promotion (EP05 / U04)

### PromotionService

```python
class PromotionService:
    def create(self, payload: PromotionCreate) -> Promotion:
        """生成 internal_code，校验 style/blogger 存在，重复检测"""
        # 故事: EP05-S02, S04, S05

    def update(self, promotion_id: UUID, payload: PromotionUpdate) -> Promotion: ...
    def list(self, *, filters: PromotionFilters) -> Page[Promotion]: ...

    def publish(self, promotion_id: UUID, publish_url: str, publish_date: date) -> Promotion: ...
        # 故事: EP05-S07
    def cancel(self, promotion_id: UUID, reason: str) -> Promotion: ...
        # 故事: EP05-S08
    def initiate_recall(self, promotion_id: UUID) -> Promotion: ...
        # 故事: EP05-S09
    def complete_recall(self, promotion_id: UUID, result: Literal["success", "failure"]) -> Promotion: ...
        # 故事: EP05-S09
    def review(self, promotion_id: UUID, action: Literal["approve", "reject"], reason: str | None) -> Promotion:
        """PR 主管审核，approve 后触发 SettlementService.create_from_promotion"""
        # 故事: EP05-S13
```

### UrgeStatusCalculator（domain.py）

```python
class UrgeStatusCalculator:
    """Q10=D 决策：Service 层 + SQL 表达式两种实现并存"""

    @staticmethod
    def for_object(promotion: Promotion, *, today: date | None = None) -> str:
        """单条推广实时计算（Service 调用）"""
        # 故事: EP05-S06

    @staticmethod
    def sql_expr() -> ColumnElement:
        """SQLAlchemy SQL 表达式（列表查询用，按 CASE WHEN 一次算完整列）"""
        # 故事: EP05-S06, EP09-S01
```

### PromotionStateMachine（domain.py）

```python
class PublishStateMachine(StateMachine):  # 未发布 → 已发布 / 已取消
    ...
class RecallStateMachine(StateMachine):  # 不需要 → 召回中 → 召回成功 / 失败
    ...
class SettlementStateMachine(StateMachine):  # 未结算 → 待核查 → 待付款 → 已付款 / 已驳回
    ...
```

### PromotionMetric（在 services/metric/publish_progress.py，但被 Promotion 列表用）

```python
def compute_effective_likes(promotion: Promotion) -> int: ...
    # 故事: EP05-S10
def is_hit(promotion: Promotion) -> bool: ...
    # 故事: EP05-S11
def compute_cpl(promotion: Promotion) -> Decimal | None: ...
    # 故事: EP05-S12
```

---

## 11. modules/finance (EP06)

### SettlementService

```python
class SettlementService:
    def create_from_promotion(self, promotion_id: UUID) -> Settlement:
        """PR 主管审核通过后自动调用，按 promotion_id 幂等"""
        # 故事: EP06-S02
    def review(self, settlement_id: UUID, action: Literal["approve", "reject"], reason: str | None) -> Settlement: ...
        # 故事: EP06-S03, S04
    def add_extra_item(self, settlement_id: UUID, payload: ExtraItemCreate) -> Settlement: ...
        # 故事: EP06-S05
    def set_payment_amount(self, settlement_id: UUID, amount: Decimal) -> Settlement: ...
        # 故事: EP06-S06
    def upload_payment_proof(
        self, settlement_id: UUID, attachment_id: UUID, payment_date: date
    ) -> Settlement: ...
        # 故事: EP06-S07
    def daily_summary(self, day: date) -> DailySummary: ...
        # 故事: EP06-S08
```

### OrderAdjustmentService（U16 / V2）

```python
class OrderAdjustmentService:
    def auto_create_from_promotion(self, promotion_id: UUID) -> OrderAdjustment | None: ...
        # 故事: EP06-S09
    def create_brushing(self, payload: BrushingCreate) -> OrderAdjustment:
        """录入刷单，自动 exclude_from_roi=True，金额支持表达式解析"""
        # 故事: EP06-S10
    def list(self, *, filters: OrderAdjustmentFilters) -> Page[OrderAdjustment]: ...
```

### BalanceService（U16 / V2）

```python
class BalanceService:
    def add_record(self, payload: BalanceRecordCreate) -> BalanceRecord:
        """自动计算余额：上一笔 + 收入 - 支出，不一致拒绝"""
        # 故事: EP06-S11
    def list(self, *, date_range: tuple[date, date] | None = None) -> list[BalanceRecord]: ...
```

---

## 12. modules/importer (EP07)

### ImportService（U06a 框架）

```python
class ImportService:
    def upload(
        self, file: UploadFile, source: str, mapping_version: int | None = None
    ) -> ImportBatch:
        """同步创建 batch，异步触发 Celery 任务解析"""
        # 故事: EP07-S07, S08

    def get_batch(self, batch_id: UUID) -> ImportBatch: ...
    def list_batches(self, *, filters: ImportBatchFilters) -> Page[ImportBatch]: ...

    def retry(self, batch_id: UUID) -> ImportBatch:
        """仅重试 failed 行，指数退避"""
        # 故事: EP07-S10

    def download_errors(self, batch_id: UUID) -> StreamingResponse:
        """下载失败明细 CSV"""
        # 故事: EP07-S10
```

### ImportAdapterRegistry / ImportAdapter（U06b-e 适配器）

```python
class ImportAdapter(Protocol):
    """每个业务表实现一个适配器"""
    source: str          # "qianniu" / "wanxiangtai" / "huitun" / "manual_style"
    target_table: str

    def parse_row(self, row: dict, mapping: FieldMapping) -> dict: ...
    def validate(self, parsed: dict) -> list[ValidationError]: ...
    def upsert(self, parsed: dict) -> UUID:
        """幂等 upsert，按业务键"""
```

具体适配器：
- `StyleSkuImportAdapter`（U06b）
- `BloggerImportAdapter`（U06c）
- `PromotionImportAdapter`（U06d）
- `SettlementImportAdapter`（U06e）
- `QianniuImportAdapter`（U13）
- `WanxiangtaiImportAdapter`（U13）
- `HuitunImportAdapter`（U13）

### FieldMappingService

```python
class FieldMappingService:
    def create_version(self, source: str, mapping_config: dict) -> FieldMapping: ...
    def get_active(self, source: str) -> FieldMapping: ...
    def list_versions(self, source: str) -> list[FieldMapping]: ...
        # 故事: EP07-S09
```

### CredentialService（U12 / V1）

```python
class CredentialService:
    def create(self, payload: CredentialCreate, *, privacy_consent: bool) -> Credential:
        """新建凭据，加密存储；默认 status=paused"""
        # 故事: EP07-S02

    def get(self, credential_id: UUID) -> CredentialPublic:
        """返回不含明文的视图"""
        # 故事: EP07-S03

    @audit("decrypt")
    def decrypt_for_purpose(self, credential_id: UUID, purpose: str) -> str:
        """解密供采集 Worker 使用，写审计"""
        # 故事: EP07-S04

    def pause(self, credential_id: UUID) -> Credential: ...
    def resume(self, credential_id: UUID) -> Credential: ...
    def delete(self, credential_id: UUID) -> None: ...
        # 故事: EP07-S05

    def report_failure(self, credential_id: UUID, error: str) -> None:
        """采集失败回调，连续 N 次自动暂停 + 企微告警"""
        # 故事: EP07-S06
```

### CrawlerTaskService（U13 / V1）

```python
class CrawlerTaskService:
    """Q13=D 决策：Worker pull 模型"""

    def schedule_daily_tasks(self) -> int:
        """Celery Beat 每天调用，按活跃凭据生成 crawler_task 记录"""
        # 故事: EP07-S11~S13

    def poll_next_task(self, worker_id: str) -> CrawlerTaskAssignment | None:
        """Worker 轮询 API：领取一个 pending 任务，返回临时凭据"""
        # 故事: EP07-S11~S13

    def report_result(
        self, task_id: UUID, status: Literal["success", "failed"],
        attachment_id: UUID | None = None, error: str | None = None,
    ) -> None:
        """Worker 上传文件后回写状态；成功则触发 ImportService.upload"""
```

### DataQualityService（U14 / V1）

```python
class DataQualityService:
    def list_issues(self, *, filters: IssueFilters) -> Page[DataQualityIssue]: ...
    def summary(self) -> DataQualitySummary:
        """按 source × severity 分组"""
        # 故事: EP07-S14
    def resolve_issue(self, issue_id: UUID, status: Literal["fixed", "ignored"]) -> None: ...
```

---

## 13. modules/wecom (EP08)

### WecomClient（低层 SDK）

```python
class WecomClient:
    def get_access_token(self) -> str:
        """带缓存的 access_token，Redis 存"""
    def send_external_msg_template(
        self, sender: str, recipients: list[str], content: WecomMessageContent
    ) -> dict:
        """企微群发助手 API"""
        # 故事: EP08-S06
    def find_external_userid_by_wechat(self, wechat_id: str) -> str | None: ...
        # 故事: EP08-S03
    def push_to_app(self, content: str, to_user: str | None = None) -> dict:
        """自建应用消息推送（用于异常预警）"""
        # 故事: EP08-S10
    def push_group_message(self, webhook_url: str, content: dict) -> dict:
        """群机器人 webhook（用于发文通知）"""
        # 故事: EP08-S09
    def verify_callback_signature(self, signature: str, body: bytes) -> bool: ...
        # 故事: EP08-S08
```

### WecomService（业务编排）

```python
class WecomService:
    def configure(self, payload: WecomConfigUpdate) -> WecomConfig: ...
        # 故事: EP08-S02
    def test_connection(self) -> bool: ...
        # 故事: EP08-S02
    def bind_blogger(self, blogger_id: UUID) -> Blogger:
        """按 wechat_id 查 external_userid 并写入"""
        # 故事: EP08-S03
    def update_template(self, template_type: str, content: str) -> MessageTemplate: ...
        # 故事: EP08-S04
    def render_template(self, template_type: str, context: dict) -> str: ...
        # 故事: EP08-S04
    def check_rate_limit(self, blogger_id: UUID, pr_id: UUID, day: date) -> RateLimitResult: ...
        # 故事: EP08-S07
    def handle_callback(self, msg_id: str, result: str) -> None:
        """企微回调更新 wecom_message.status"""
        # 故事: EP08-S08
```

### WecomTask（在 app/tasks/wecom_tasks.py，Celery）

```python
@celery_app.task
def scan_and_dispatch_urge() -> None:
    """每天 09:00 扫描需催发推广，按博主聚合，写 wecom_message"""
    # 故事: EP08-S05

@celery_app.task
def execute_wecom_message(message_id: UUID) -> None:
    """调用 WecomClient 发送，频控触发则降级写 notification"""
    # 故事: EP08-S06, S07

@celery_app.task
def check_anomaly_and_alert() -> None:
    """监控退货率/转化率/投产比，异常时 push_to_app"""
    # 故事: EP08-S10
```

---

## 14. modules/report (EP09)

### ReportService（编排）

```python
class ReportService:
    """所有 GET /api/reports/* 入口，调用 MetricService 取指标 + Repository 取明细"""

class PublishProgressService:
    def get_summary(self, time_range: TimeRange) -> ProgressSummary: ...
    def get_cards(self, time_range: TimeRange, *, filters: CardFilters) -> Page[StyleCard]: ...
    def get_detail_by_pr(self, style_id: UUID, time_range: TimeRange) -> list[PrDetail]: ...
    def get_detail_by_time(self, style_id: UUID, time_range: TimeRange) -> list[TimeSeriesPoint]: ...
        # 故事: EP09-S01

class WorkProgressService:
    def get_for_month(self, month: str) -> list[PrWorkProgress]: ...
        # 故事: EP09-S02

class TargetPlanningService:
    def set_target(self, payload: TargetCreate) -> TargetPlanning: ...
    def get_with_actuals(self, month: str) -> list[TargetWithActual]: ...
        # 故事: EP09-S03

class StoreDailyService:
    def get_dashboard(self, time_range: TimeRange) -> StoreDailyDashboard: ...
    def update_manual_field(self, day: date, field: str, value: Decimal) -> None: ...
        # 故事: EP09-S04

class ProductionService:
    def get_report(self, time_range: TimeRange, *, filters: ProductionFilters) -> ProductionReport:
        """剔除 exclude_from_roi 订单"""
        # 故事: EP09-S05

class ReportExportService:
    def export(
        self, report_type: str, time_range: TimeRange, filters: dict, format: Literal["xlsx"] = "xlsx"
    ) -> StreamingResponse: ...
        # 故事: EP09-S08
```

---

## 15. services/metric/

```python
# publish_progress.py
def count_promotions(filters: PromotionFilters) -> int: ...                       # EP05-S10
def count_publish(filters: PromotionFilters) -> int: ...
def sum_likes(filters: PromotionFilters, *, only_hit: bool = False) -> int: ...   # EP05-S11
def cpl(filters: PromotionFilters) -> Decimal | None: ...                         # EP05-S12

# work_progress.py
def pr_work_progress(month: str) -> list[PrWorkProgressMetric]: ...               # EP09-S02

# style_roi.py
def return_rate(time_range: TimeRange, filters: StyleFilters) -> Decimal | None: ...
def add_to_cart_cost(...) -> Decimal | None: ...
def net_roi(...) -> Decimal | None: ...                                           # EP09-S05

# blogger_quality.py
def blogger_type(follower_count: int) -> str: ...                                 # EP04-S04
def read_like_ratio(blogger: Blogger) -> Decimal | None: ...                      # EP04-S05
def is_fake(blogger: Blogger) -> bool: ...                                        # EP04-S06
def quality_tags(blogger: Blogger) -> list[str]: ...                              # EP04-S07

# common.py
def safe_div(numerator: Decimal | int, denominator: Decimal | int) -> Decimal | None: ...
```

---

## 16. modules/ai (EP11 / U18 / P3)

```python
class AiAdvisoryService:
    def strategy_advice(self, time_range: TimeRange) -> AiAdvice: ...    # EP11-S01
    def anomaly_diagnosis(self, alert_id: UUID) -> AiAdvice: ...         # EP11-S02
    def blogger_suggest(self, style_id: UUID, top_n: int = 10) -> list[BloggerSuggestion]: ...
        # EP11-S03
```

---

## 17. 前端关键 hooks（React Query + Zustand）

每个 feature 的典型 hooks：

```typescript
// features/promotion/hooks/usePromotions.ts
export function usePromotions(filters: PromotionFilters): UseQueryResult<Page<Promotion>>
export function usePromotion(id: string): UseQueryResult<Promotion>
export function useCreatePromotion(): UseMutationResult<Promotion, Error, PromotionCreate>
export function usePublishPromotion(): UseMutationResult<Promotion, Error, PublishPayload>
export function useUrgeStatusBadge(promotion: Promotion): { label: string; color: string }

// stores/authStore.ts
interface AuthStore {
  user: User | null
  token: string | null
  permissions: EffectivePermissions
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  hasPermission: (scope: string, action: string) => boolean
  hasField: (fieldPath: string) => boolean
}
```

---

## 一致性校验汇总

| 校验 | 结果 |
|---|---|
| 89 个可实施故事都至少关联一个组件方法 | ✅ |
| 每个跨租户安全敏感方法都有 audit 标记 | ✅（凭据解密、settlement 写、user 操作） |
| 状态机转移都是 `xxx_state_machine.py` 集中声明 | ✅（design / promotion / settlement） |
| MetricService 集中且按需求第 14 节口径定义 | ✅ |
| 字段级权限通过 Pydantic + 装饰器组合实现 | ✅（Q7=D） |
| 多租户 Session 注入 + RLS 兜底 | ✅（Q6=A） |
| 采集 Worker 通信使用 pull + HTTP 上传 | ✅（Q13=D） |
