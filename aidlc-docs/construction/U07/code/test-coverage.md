# U07 测试覆盖（企微集成基础）

> Build & Test：Docker（PG16:5549 + Redis7:6404 + Python 3.12-slim）
> 结果：**612 passed, 11 deselected, 79.20% 覆盖率**（门槛 70%）；U07 新增 36 例（19 unit + 17 integration）

---

## 1. 单元测试（19 例）

| 文件 | 用例 |
|---|---|
| `test_crypto_wecom.py`（5） | round-trip / 跨租户密钥不可解 / tag 篡改失败 / 空密文失败 / nonce 唯一 |
| `test_wecom_domain.py`（10） | extract/validate 白名单 / render 替换+缺值空串 / is_important（3天内/超时/催发区间）/ build_render_ctx（含无日期） |
| `test_wecom_message_status.py`（4） | 6 态枚举 / 模板类型 / 通知类型 / 回调结果映射 |

## 2. 集成测试（17 例，WecomClient mock）

| 文件 | 用例 |
|---|---|
| `test_wecom_config.py`（3） | secret 加密落库不回显 + 解密一致 / test_connection ok / 更新覆盖单条 |
| `test_wecom_bind.py`（4） | 绑定成功 / 无微信 422 / 未匹配 404 / 未配置 409 |
| `test_wecom_scan.py`（2） | 候选聚合建 pending（含昵称渲染）+ 幂等 / 未绑定通知 PR |
| `test_wecom_send.py`（3） | 正常 created+msgid / 博主频控 rate_limited 未调企微 / 非 pending 跳过 |
| `test_wecom_callback.py`（3） | 签名通过 sent+sent_at / 签名失败 403 / 未知 msgid 忽略 |
| `test_notification.py`（2） | notify+unread+mark_read / 他人 mark_read 拒绝 |

---

## 3. 故事追溯矩阵

| 故事 | 测试 | 结果 |
|---|---|---|
| EP08-S02 配置 | test_wecom_config（加密不回显 + test_connection） | ✅ |
| EP08-S03 绑定 | test_wecom_bind（4 分支） | ✅ |
| EP08-S04 模板 | test_wecom_domain（白名单）+ template_service | ✅ |
| EP08-S05 扫描 | test_wecom_scan（聚合 + 幂等 + 未绑定） | ✅ |
| EP08-S06 群发 | test_wecom_send（created）+ test_wecom_callback（created→sent） | ✅ |
| EP08-S07 频控降级 | test_wecom_send（rate_limited）+ test_notification | ✅ |
| EP08-S08 回调 | test_wecom_callback（签名 + 幂等） | ✅ |

## 4. 设计守护测试矩阵

| 守护 | 测试 | 结果 |
|---|---|---|
| P-U07-01 凭据加密（每租户密钥 + tag 防篡改） | test_crypto_wecom | ✅ |
| P-U07-03 扫描幂等 + 未绑定跳过 | test_wecom_scan | ✅ |
| P-U07-04 频控 DB 计数降级 | test_wecom_send | ✅ |
| P-U07-05 回调签名 + 幂等推进 | test_wecom_callback | ✅ |
| 模板变量白名单 | test_wecom_domain | ✅ |
| 通知限本人 | test_notification | ✅ |

---

## 5. Build & Test 过程

- Docker（PG16:5549 + Redis7:6404 + Py3.12 + u07_net + u07_pipcache）；alembic 001→011 全链路成功。
- U07 子集 36 passed；全量回归 **612 passed / 0 failed / 11 deselected**；覆盖率 **79.20%**。
- **修复 2 个真实 bug**：
  1. `client.py` 回调签名用了 `hashlib.compare_digest`（不存在）→ 改 `hmac.compare_digest`。
  2. `notification_api` mark_read 用 `status_code=204` + `-> None` → FastAPI 0.115 断言 "204 must not have a response body" 导致**整个 app 构造失败**（连带所有 tests/api 失败）→ 改 200 返回 `{"ok": true}`。
- 另修 2 个测试断言：send 测试 `session.refresh` 丢弃 service 未 flush 的内存变更 → 改 `session.flush()` 后断言。
- 清理临时容器/网络/卷 + 临时脚本。

## 6. 已知无害告警
- 测试结束后 redis `AbstractConnection.__del__` 的 `RuntimeError: Event loop is closed` 为已知无害告警（exit 0 等价，612 passed）。
