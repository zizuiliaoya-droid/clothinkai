# U15 NFR 设计模式（企微进阶）

> 5 个设计模式伪代码：S09 控评通知（listener + task + service）/ S10 监控逐租户容错 / S10 异常判定去重推送 / 配置管理 / 客户端 2 方法。
> 复用 U07 send_service/scan_service 的 Celery + best-effort + 逐租户模式。

---

## P-U15-01：S09 发文通知控评（listener → Celery → GroupNotifyService）

### listener（事务内仅入队，不 HTTP）
```python
# modules/wecom/listeners.py（追加）
async def on_promotion_published(event: PromotionPublished, session) -> None:
    # 事务内：不做 HTTP / 不写库，仅入队（HTTP 在任务内异步）
    from app.tasks.wecom_tasks import notify_control_group
    notify_control_group.delay(str(event.promotion_id), str(event.tenant_id))

def register() -> None:
    subscribe("SettlementPaid", on_settlement_paid)      # U04 既有
    subscribe("PromotionPublished", on_promotion_published)  # U15 新增
```

### Celery 任务（重读校验防回滚误发）
```python
# tasks/wecom_tasks.py（追加）
@celery_app.task(bind=True, name="...notify_control_group", queue="default",
                 autoretry_for=(OperationalError,), max_retries=1, default_retry_delay=5)
def notify_control_group(self, promotion_id: str, tenant_id: str) -> dict:
    return asyncio.run(_notify_control_group(UUID(promotion_id), UUID(tenant_id)))

async def _notify_control_group(promotion_id, tenant_id) -> dict:
    tok = tenant_id_ctx.set(tenant_id)
    try:
        async with system_context(), AsyncSessionApp() as s:
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                            {"t": str(tenant_id)})
            result = await GroupNotifyService(s).notify_publish(promotion_id, tenant_id)
            await s.commit()
        return result
    finally:
        tenant_id_ctx.reset(tok)
```

### GroupNotifyService.notify_publish（best-effort 4 分支）
```python
async def notify_publish(self, promotion_id, tenant_id) -> dict:
    promo = await PromotionRepository(self._s).get_by_id(promotion_id)
    # 防回滚误发：publish 事务可能已回滚 / 状态被改
    if promo is None or promo.publish_status != "已发布" or not promo.publish_url:
        return {"status": "skipped"}
    cfg = await self._alert_cfg.get()
    if cfg is None or not cfg.control_group_webhook:
        log.warning("control_group_webhook_unconfigured", extra={"tenant_id": str(tenant_id)})
        wecom_group_notify_total.labels(status="unconfigured").inc()
        return {"status": "unconfigured"}
    blogger = await BloggerRepository(self._s).get_by_id(promo.blogger_id)
    markdown = (
        f"**新笔记发布·待控评**\n"
        f"> 款号：{promo.style_code_snapshot}（{promo.internal_code}）\n"
        f"> 博主：{blogger.nickname if blogger else promo.blogger_id}\n"
        f"> 链接：[{promo.publish_url}]({promo.publish_url})\n"
        f"> 日期：{promo.actual_publish_date}"
    )
    http = build_http_client()
    try:
        client = WecomClient(tenant_id, cfg_wecom, http=http, secret_provider=...)  # group_robot 无需 token
        await client.send_group_robot(cfg.control_group_webhook, markdown)
        wecom_group_notify_total.labels(status="sent").inc()
        return {"status": "sent"}
    except (WecomApiError, WecomRateLimited, httpx.HTTPError) as exc:  # best-effort
        log.warning("group_notify_failed", extra={"tenant_id": str(tenant_id), "err": str(exc)})
        wecom_group_notify_total.labels(status="failed").inc()
        return {"status": "failed"}
    finally:
        await http.aclose()
```
> 注：send_group_robot 无需 access_token，可不依赖 WecomConfig；实现时 client 仅用 http。
> 不抛错（best-effort），发布主流程已完成（BR-U15-02/04/05）。

---

## P-U15-02：S10 监控任务逐租户容错（check_anomaly_and_alert）

```python
@celery_app.task(name="...check_anomaly_and_alert", queue="default")
def check_anomaly_and_alert() -> dict:
    return asyncio.run(_check_all())

async def _check_all() -> dict:
    async with AsyncSessionBypass() as meta:
        tenant_ids = list((await meta.execute(text(
            "SELECT tenant_id FROM wecom_alert_config WHERE is_enabled = true"
        ))).scalars().all())
    total = 0
    for tid in tenant_ids:
        tok = tenant_id_ctx.set(tid)
        try:
            async with system_context(), AsyncSessionApp() as s:
                await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                                {"t": str(tid)})
                n = await AnomalyAlertService(s).check_and_alert(tid)
                await s.commit()
                total += n
        except Exception as exc:  # noqa: BLE001 — 单租户失败不中止其余
            log.exception("anomaly_check_failed", extra={"tenant_id": str(tid)})
            sentry_sdk.capture_exception(exc)
        finally:
            tenant_id_ctx.reset(tok)
    return {"tenants": len(tenant_ids), "alerts": total}
```
> 复用 U07 _scan_and_dispatch / U13 schedule 的 bypass 元数据读 + 逐租户 set_config 模式（BR-U15-60/61）。

---

## P-U15-03：S10 异常判定 + 去重 + 推送（AnomalyAlertService.check_and_alert）

```python
_WINDOW = "last_7d"

async def check_and_alert(self, tenant_id) -> int:
    cfg = await self._alert_cfg.get()        # 实时阈值（无缓存，即时生效 BR-U15-24）
    if cfg is None or not cfg.is_enabled:
        return 0
    tr = resolve_time_range(_WINDOW, None, None)
    report = await ProductionService(self._s).get_report(tenant_id, tr)  # 复用 U14
    sent = 0
    for row in report.items:
        for alert_type, detail in self._evaluate_row(row, cfg):
            sent += await self._fire(tenant_id, alert_type, row, detail, cfg)
    return sent

@staticmethod
def _evaluate_row(row, cfg) -> list[tuple[str, dict]]:
    out = []
    if row.return_rate is not None and row.return_rate > cfg.return_rate_threshold:
        out.append(("return_rate_high",
                    {"value": str(row.return_rate), "threshold": str(cfg.return_rate_threshold)}))
    if cfg.low_roi_threshold is not None and row.net_roi is not None \
       and row.net_roi < cfg.low_roi_threshold:
        out.append(("roi_low",
                    {"value": str(row.net_roi), "threshold": str(cfg.low_roi_threshold)}))
    # conversion_low：V1 口径缺失占位，不检（BR-U15-23）
    return out

async def _fire(self, tenant_id, alert_type, row, detail, cfg) -> int:
    period_key = get_today().isoformat()
    if await self._log_repo.exists(alert_type=alert_type, entity_ref=str(row.style_id),
                                   period_key=period_key):
        wecom_anomaly_alert_total.labels(alert_type=alert_type, status="deduped").inc()
        return 0
    if not cfg.alert_recipients:
        wecom_anomaly_alert_total.labels(alert_type=alert_type, status="no_recipient").inc()
        return 0   # 不落 log（配置补齐后可补推 BR-U15-27）
    markdown = self._render(alert_type, row, detail)
    http = build_http_client()
    try:
        client = WecomClient(tenant_id, await self._wecom_cfg(), http=http,
                             secret_provider=self._secret_provider(tenant_id))
        await client.send_app_message(cfg.alert_recipients, markdown)
        self._log_repo.add(WecomAlertLog(
            alert_type=alert_type, entity_type="style", entity_ref=str(row.style_id),
            period_key=period_key, detail={**detail, "style_code": row.style_code}))
        await self._s.flush()   # 成功才落 log（IntegrityError 并发 → deduped）
        wecom_anomaly_alert_total.labels(alert_type=alert_type, status="sent").inc()
        return 1
    except IntegrityError:
        wecom_anomaly_alert_total.labels(alert_type=alert_type, status="deduped").inc()
        return 0
    except (WecomApiError, WecomRateLimited, httpx.HTTPError):  # 不落 log，可重试 BR-U15-28
        wecom_anomaly_alert_total.labels(alert_type=alert_type, status="failed").inc()
        return 0
    finally:
        await http.aclose()

@staticmethod
def _render(alert_type, row, detail) -> str:
    titles = {"return_rate_high": "退货退款率过高", "roi_low": "净投产比过低"}
    advice = {"return_rate_high": "建议核查款式质量/详情页/物流",
              "roi_low": "建议优化投放或暂停低效推广"}
    return (f"**异常预警·{titles[alert_type]}**\n"
            f"> 款号：{row.style_code} {row.style_name}\n"
            f"> 当前值：{detail['value']}（阈值：{detail['threshold']}）\n"
            f"> 建议：{advice[alert_type]}")
```

---

## P-U15-04：配置管理（AlertConfigService upsert + 脱敏 + 校验）

```python
async def upsert(self, payload: AlertConfigUpdate, user) -> WecomAlertConfig:
    # 校验（BR-U15-41/42/43）
    if not (0 <= payload.return_rate_threshold <= 1):
        raise AlertConfigInvalidError("return_rate_threshold 须 ∈ [0,1]")
    if payload.low_roi_threshold is not None and payload.low_roi_threshold <= 0:
        raise AlertConfigInvalidError("low_roi_threshold 须 > 0")
    if payload.control_group_webhook and not payload.control_group_webhook.startswith("https://"):
        raise AlertConfigInvalidError("webhook 须为 https URL")
    recipients = list(dict.fromkeys(payload.alert_recipients))  # 去重保序
    stmt = pg_insert(WecomAlertConfig).values(
        tenant_id=user.tenant_id, control_group_webhook=payload.control_group_webhook,
        return_rate_threshold=payload.return_rate_threshold,
        low_roi_threshold=payload.low_roi_threshold,
        low_conversion_threshold=payload.low_conversion_threshold,
        alert_recipients=recipients, is_enabled=payload.is_enabled,
    ).on_conflict_do_update(index_elements=["tenant_id"], set_={...,"updated_at": func.now()}
    ).returning(WecomAlertConfig)
    row = (await self._s.execute(stmt)).scalar_one()
    await AuditService(self._s).log("wecom.alert_config.update", resource="wecom_alert_config",
                                    resource_id=row.id, user_id=user.id)
    await self._s.commit()
    return row

async def get_response(self) -> AlertConfigResponse | None:
    cfg = await self._repo.get()
    if cfg is None:
        return None
    wh = cfg.control_group_webhook
    return AlertConfigResponse(
        webhook_configured=bool(wh),
        webhook_mask=(("***" + wh[-6:]) if wh else None),   # 脱敏 BR-U15-44
        return_rate_threshold=cfg.return_rate_threshold,
        low_roi_threshold=cfg.low_roi_threshold,
        low_conversion_threshold=cfg.low_conversion_threshold,
        alert_recipients=cfg.alert_recipients, is_enabled=cfg.is_enabled,
    )
```

---

## P-U15-05：WecomClient 2 方法

```python
async def send_group_robot(self, webhook_url: str, markdown: str) -> dict:
    # 群机器人：直连完整 webhook URL（含 key），无 access_token
    data = (await self._http.post(
        webhook_url, json={"msgtype": "markdown", "markdown": {"content": markdown}}
    )).json()
    if data.get("errcode"):
        raise WecomApiError(data["errcode"], data.get("errmsg"))
    return data

async def send_app_message(self, touser: list[str], markdown: str) -> dict:
    # 自建应用：复用 _call（token 刷新 + 频控）+ 计时
    with wecom_send_duration_seconds.time():
        return await self._call("POST", "/cgi-bin/message/send", json={
            "touser": "|".join(touser),
            "agentid": int(self._cfg.agent_id),
            "msgtype": "markdown", "markdown": {"content": markdown},
        })
```

---

## 故事 / NFR 映射

| 模式 | 故事/NFR | 规则 |
|---|---|---|
| P-U15-01 | EP08-S09 | BR-U15-01~07 |
| P-U15-02 | EP08-S10 + NFR06 | BR-U15-60/61 |
| P-U15-03 | EP08-S10 | BR-U15-20~29 |
| P-U15-04 | EP08-S10 配置 | BR-U15-40~44 |
| P-U15-05 | S09/S10 客户端 | BR-U15-07/26 |
