# 调研报告 D：登录 IP 白名单（DDNS 方案，上线默认关闭）

> 2026-10-05 · `main` @ `a0f1410`（alembic head `056_goods_short_name`）· 只读调研：未改代码、未连 Zeabur、未发生产请求。
> 依据：PRD V1.4 改动 7（`docs/requirements/LENNEA店铺系统 PRD V1.4.docx`）、台账 `docs/requirements/PRD-V1.4-实施批次.md:752-760`（批次 6 IP 白名单）与 `:806`、`:811`（待确认 / 已关闭）。

## 0. 结论

IP 白名单在代码里是零实现：没有表、接口、页面，也没有解析任务。比白名单本身更要紧的是它的前提：**后端现在大概率拿不到真实出口 IP**。启动命令只带 `--proxy-headers`，uvicorn 0.30.6 默认只信任 `127.0.0.1` 发来的转发头，仓库和部署记录里都没有 `FORWARDED_ALLOW_IPS`，所以 `request.client.host` 很可能是 Zeabur 入口的内网地址。在这个前提下开白名单，要么全员被拦，要么（有人把入口地址填进名单）全员放行；今天的 `audit_log.ip`、登录失败限流、全局限流也都建立在这个错误的 IP 上。

建议顺序：先跑只读 SQL，再上一个管理员专用诊断端点看清转发链；然后升级 uvicorn（0.30.6 不支持按网段信任代理）并按实测配置可信代理；最后才做白名单本体（默认关闭，关闭期间照样记录每次登录「是否命中」，作为业务开启前的证据）。

计数（共 14 条，见主表）：**已满足 1 · 部分满足 4 · 缺失 8 · 冲突 0 · 待业务确认 1**。白名单整体估 **8~11 人日**（含测试，按第 6 节清单：4 项 M、其余 S），明显超出台账批次 6 整批「约 4 人日」的估计。

## 1. 主表

| 编号 | 来源 | 需求（摘） | 现状（证据） | 判断 | 建议改动 | 工作量 | 依赖 / 风险 |
|---|---|---|---|---|---|---|---|
| D-01 | PRD 改动 7 | 校验「当前出口 IP」，前提是后端拿得到真实 IP | 取 IP 用 slowapi `get_remote_address` = `request.client.host`（`backend/app/modules/auth/api.py:76`）；`backend/Dockerfile:43` 只有 `--proxy-headers`；全仓无 `FORWARDED_ALLOW_IPS`；uvicorn 锁 0.30.6（`backend/requirements.txt:8`），默认只信任 127.0.0.1 | 缺失（推断，需生产核对） | 后端：管理员诊断端点；升级 uvicorn ≥0.31；Zeabur 环境变量 `FORWARDED_ALLOW_IPS=<实测网段>`，禁用 `*` | S | **最高风险**：配错就能被伪造 `X-Forwarded-For` 绕过；`*.zeabur.app` 前面可能还有一层边缘网关（`docs/ZEABUR_SETUP.md:125`） |
| D-02 | PRD 改动 7 | 名单内正常登录；名单外拒绝并提示「仅支持办公室网络访问，如需远程联系管理员开通」 | `AuthService.login`（`backend/app/modules/auth/service.py:101-230`）没有任何 IP 判断 | 缺失 | 后端：查到用户（定租户）之后、判锁定 / 验密码之前判定；新异常 403 `LOGIN_IP_NOT_ALLOWED`。前端：`LoginForm.tsx:36` 已直接显示后端 message，不用改 | M | 依赖 D-01、D-03 |
| D-03 | PRD 改动 7 | 名单由管理员在后台配置 | 「系统设置」页只有企微（`frontend/src/features/settings/api.ts:24-54` → `/api/settings/wecom`）；无表、无接口 | 缺失 | 迁移：`login_ip_policy` + `login_ip_entry`（租户级 + RLS）；后端 CRUD；权限 `security.ip_allowlist:write`；前端设置页加 Tab | M（后端）+ M（前端） | 一级域用 `security`，避开 `auth.*` 通配 |
| D-04 | 业务 10-03 / 10-05 | 动态 IP：名单填 DDNS 域名，后端定时解析 | Beat 里没有相关任务（`backend/app/core/celery_app.py:73` 起的 `beat_schedule`） | 缺失 | `*/5` 解析任务，结果落库；失败沿用上次结果并告警；保存 / 开启时同步解析一次 | M | 路由器 WAN 口必须是公网 IP（Q6）；IPv6（Q5） |
| D-05 | 业务 10-05 | 上线默认关闭，确认解析正常后由管理员开启 | — | 缺失 | `login_ip_policy.enabled` 默认 false；关闭期间照样记录每次登录是否命中（试运行） | S | Q4 |
| D-06 | 台账护栏 | 环境变量总开关（kill switch） | 无；有先例 `REPORT_SUMMARY_READS_ENABLED`（`backend/app/core/config.py:161`） | 缺失 | `LOGIN_IP_ALLOWLIST_ENABLED: bool = True`；生效 = 环境变量 AND 后台开关；另备一条应急 SQL | S | 改环境变量要重启 backend |
| D-07 | 台账护栏 | 防自锁：名单为空不启用；开启 / 保存时当前管理员 IP 不在名单里就拒绝 | 无 | 缺失 | 开启、以及开启状态下每次改条目，都用本次请求的真实 IP 自检；管理员 IP 不是公网地址时拒绝开启 | S | 依赖 D-01 |
| D-08 | 台账护栏 | 管理员临时放行（IP + 到期时间） | 无 | 缺失 | `login_ip_entry.expires_at`（仅 ip / cidr），必填原因，默认 24 小时、最长 7 天；拒绝提示里带上用户自己的 IP | S | Q2、Q3 |
| D-09 | 台账护栏 | 不设免检账号 | 登录流程没有任何按账号豁免的逻辑（`auth/service.py:101-230`） | 已满足（设计保持） | 设计里不出现按账号豁免；`platform_admin` 同样受限 | — | — |
| D-10 | PRD 改动 7 | 登录失败（IP 不符 / 密码错）写安全日志，含 IP、时间、账号 | 不存在 / 锁定 / 禁用 / 密码错 / 限流都写 `audit_log`（`auth/service.py:119-126, 136-143, 149-157, 161-169, 177-185`），有时间；但 IP 是 D-01 的入口地址；账号：不存在时记 `after.username`，存在时只有 `user_id`，限流时完全没有；`tenant_id` 推断全为空（`backend/app/core/audit.py:67` 只从上下文取，登录请求没有上下文） | 部分满足 | 登录类审计统一写 `after.username` + 原因 + 显式 `tenant_id`；新增 `login_ip_denied` | S | 依赖 D-01，否则 IP 仍是错的 |
| D-11 | PRD 改动 7 | 安全日志有地方看 | 只有 `GET /api/audit-logs`（`auth/api.py:383-388`，`auth.audit:read`），前端零页面（`frontend/src` 搜 `audit-logs` 0 命中）；查询不按租户过滤（`auth/repository.py:276` 起；`AuditLog` 不是 `TenantScopedModel`，`auth/models.py:233`；`audit_log` 没开 RLS，`alembic/versions/002_u01_enable_rls.py:29-34`） | 部分满足 | 新端点 `GET /api/security/login-logs`（固定 action 集合 + 显式租户过滤，沿用 `auth.audit:read`）；设置页加「登录安全日志」Tab | S + S | 生产核对谁持有 `auth.*:read`（SQL-6） |
| D-12 | PRD 改动 7 只写「登录」 | 已登录会话换了 IP 怎么办 | 刷新端点不取 IP（`auth/api.py:89-101`）、不审计，新 `refresh_token` 行不记 ip（`auth/service.py:294-302`）；refresh 7 天滑动续期、无绝对上限（`core/config.py:68`，`auth/service.py:279-302`）：办公室登录的会话带回家可以一直续 | 待业务确认 | 推荐「登录 + 刷新 token 时校验」：离开办公室后 ≤30 分钟失效；拒绝时吊销该 jti、审计 `token_refresh_ip_denied`，前端把原因带到登录页 | S | Q1；办公室换 IP 的窗口期内，在线的人会陆续掉线 |
| D-13 | U01 设计（L2） | 登录端点按 IP 每分钟 20 次 | `auth/api.py:5` 注释与 `aidlc-docs/construction/U01/nfr-design/nfr-design-patterns.md:216-232` 都写了，代码里没有任何 `@limiter.limit`；`RATE_LIMIT_LOGIN_IP`（`core/config.py:133`）无人引用 | 部分满足（L1 / L3 / L4 在，L2 缺） | 修好真实 IP 后补上 L2，顺带压住被拒登录刷审计日志 | S | 依赖 D-01 |
| D-14 | 关联功能 | 采集 Worker Token 的 IP 白名单有效 | 同样用 `request.client.host`（`backend/app/modules/collect/deps.py:48`），匹配逻辑在 `collect/worker_token_service.py:42-54` | 部分满足 | 随 D-01 一起变对，无额外改动；把 `ip_is_allowed` 提到 `core` 给登录复用 | — | 采集器生产上从没跑过（台账 5b-2），所以问题没暴露 |

## 2. 逐条回答

### D1 登录链路

**登录端点** `POST /api/auth/login`（`auth/api.py:69-86`）用 `BypassSessionDep`（`backend/app/core/db.py:174` `get_bypass_session`）：登录时还不知道租户，按用户名跨租户查人，所以走绕过 RLS 的连接。IP 在 `auth/api.py:76` 取：`get_remote_address(request)`。

**`AuthService.login`**（`auth/service.py:101-230`）的顺序：

1. L3：Redis 键 `login:fail:{ip}:{username}`（`service.py:78-79`）计数 ≥ `LOGIN_FAIL_LIMIT_PER_IP_USERNAME`=5（`config.py:135`）时，审计 `login_rate_limited`，返回 429 `RATE_LIMITED`（`service.py:115-128`）。计数首次出现时设 TTL 900 秒（`config.py:134`，`service.py:232-235`），登录成功清零（`service.py:190`）
2. 按用户名查用户（`service.py:131`；`auth/repository.py:40-43`，不带租户条件）
3. 用户不存在：计 L3，审计 `login_failed`（`actor_type=unknown`，`after={"username": …}`），401 `INVALID_CREDENTIALS`（`service.py:133-145`）
4. 已锁定：审计 `login_locked`，423 `ACCOUNT_LOCKED`；禁用或软删：审计 `login_disabled`，401 `ACCOUNT_DISABLED`（`service.py:147-171`）。这两步在验密码**之前**
5. 密码错：计 L3 + L4（`user.failed_login_count` 到 `ACCOUNT_LOCK_THRESHOLD`=10 就锁号、刷新安全戳、吊销全部 refresh、审计 `user_lock`，`service.py:237-261`），再审计 `login_failed`，401（`service.py:173-187`）
6. 成功：清 L3，签发 access（30 分钟）+ refresh（7 天），`refresh_token` 行记 ip / UA（`service.py:209-219`），审计 `login`（`service.py:220-228`）

**四层限流的实际状态**

| 层 | 设计 | 现状 |
|---|---|---|
| L1 全局 | slowapi `100/minute` | 在：`main.py:82-87` + `SlowAPIMiddleware`（`main.py:404`），key = `get_remote_address`，按「端点 + key」分桶（[slowapi 0.1.9 extension.py](https://raw.githubusercontent.com/laurentS/slowapi/v0.1.9/slowapi/extension.py) 里 `limit_scope = lim.scope or endpoint`） |
| L2 登录端点 IP | `20/minute` | **没有**：`auth/api.py:5` 注释和 U01 设计都写了，全仓没有任何 `@limiter.limit`，`RATE_LIMIT_LOGIN_IP`（`config.py:133`）无人引用 |
| L3 (IP, 用户名) | 15 分钟 5 次 | 在（如上） |
| L4 账户累计 | 10 次锁号 | 在（如上），管理员 `PUT /api/users/{id}/unlock` 解锁 |

**审计 action 名**（BR-AUDIT-001，`aidlc-docs/audit.md:686`）：`login`、`login_failed`、`login_locked`、`login_disabled`、`login_rate_limited`、`user_lock`、`logout`、`password_change`、`password_change_failed`、`password_reset`、`user_create` / `user_update` / `user_toggle` / `user_unlock`、`role_assign`，以及 `permission.grant` / `permission.revoke`。

**刷新** `POST /api/auth/refresh`（`auth/api.py:89-101`）不接收 `Request`，拿不到 IP。`AuthService.refresh`（`service.py:266-304`）校验 refresh JWT、jti 未吊销（Redis 黑名单 + 库）、用户 active 且未锁定，然后吊销旧 jti、换发新的 7 天 refresh：**滑动续期，没有绝对上限**；新 `refresh_token` 行不记 ip / UA（`service.py:294-302`），也不写审计。前端遇到任何 401 都自动刷新，刷新失败清 token 并跳登录（`frontend/src/services/apiClient.ts:69-73, 101, 114-119`；`frontend/src/App.tsx:115-116`）。

**登出**（`auth/api.py:104-121`）吊销该用户全部 refresh 并审计 `logout`；access token 不进黑名单，等它自然过期。**鉴权依赖** `get_current_user`（`auth/deps.py:34-66`）只做验签 / 黑名单 / 状态 / 安全戳，没有 IP 概念。

### D2 客户端 IP 怎么取（最大风险）

#### 2.1 代码里取 IP 的所有地方

| 位置 | 取法 | 用途 |
|---|---|---|
| `auth/api.py:76` | `get_remote_address(request)`：就是 `request.client.host`，取不到时回落 `"127.0.0.1"`（[slowapi 0.1.9 util.py](https://raw.githubusercontent.com/laurentS/slowapi/v0.1.9/slowapi/util.py)） | 传给 `AuthService.login` 的 `ip` |
| `main.py:82-83` | 同一个函数做 `key_func` | L1 全局限流 |
| `auth/service.py:113` | 上面的 ip 拼进 `login:fail:{ip}:{username}` | L3 |
| `auth/service.py:124, 141, 155, 167, 183, 226, 259` | 写入 `audit_log.ip` | 登录类 6 个 action |
| `auth/service.py:217` | 写入 `refresh_token.ip` | 只在登录签发时；刷新换发的为空（`:294-302`） |
| `collect/deps.py:48` | `request.client.host` | 采集 Worker Token 的 IP 白名单（`worker_token_service.py:104-117`） |

全仓没有任何读 `X-Forwarded-For` / `X-Real-IP` / `CF-Connecting-IP` 的代码（grep 0 命中）。其余审计（登出、改密、用户管理、权限、业务操作）的 `ip` 都是空：`AuditService.log` 的 `ip` 参数默认 `None`（`core/audit.py:44-58`），只有登录路径传了。所以「`audit_log.ip` 谁填的」的答案是：只有登录相关的 6 个 action，填的是 `request.client.host`。

#### 2.2 Zeabur 入口转发后，`request.client.host` 是什么

证据链：

1. 启动命令 `backend/Dockerfile:43`：`uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers`，没有 `--forwarded-allow-ips`。本地部署记录 `deploy-secrets.local.md:130`（gitignore，未入库）记的生产 backend 启动参数与此相同；`docker-compose.yml:48` 也一样
2. `FORWARDED_ALLOW_IPS` 在仓库、`.env.example`、`docs/ZEABUR_SETUP.md`、本地部署记录里都没有。Zeabur 控制台里的环境变量本次没看（约束不连 Zeabur），列为生产核对
3. uvicorn 0.30.6 的源码：没传参数时读环境变量 `FORWARDED_ALLOW_IPS`，再没有就是 `"127.0.0.1"`（[config.py](https://raw.githubusercontent.com/encode/uvicorn/0.30.6/uvicorn/config.py)）；`ProxyHeadersMiddleware` 只有 TCP 对端在信任集合里才用 `X-Forwarded-For` 改写 `scope["client"]`。这个版本的信任集合是**字符串精确匹配**，不支持网段；配 `*` 时直接取 XFF **最左边**一项（[proxy_headers.py](https://raw.githubusercontent.com/encode/uvicorn/0.30.6/uvicorn/middleware/proxy_headers.py)）。按网段信任是 0.31.0 才加的（[0.31.0 proxy_headers.py](https://raw.githubusercontent.com/encode/uvicorn/0.31.0/uvicorn/middleware/proxy_headers.py)）
4. Zeabur 官方 Caddy 模板说明：入口代理会给请求加 `X-Forwarded-For` 与 `X-Real-IP`，并建议按私网段信任这一层代理（[Zeabur Caddy 模板](https://zeabur.com/templates/FFDLWU)）。也就是说入口连到容器时，对端是私网地址，不是 127.0.0.1

**结论（推断，把握较高，未在生产验证）**：生产上 `request.client.host` 是入口的私网地址，转发头被原样忽略。

两个让情况更复杂的点：

- `docs/ZEABUR_SETUP.md:125` 记过「域名在边缘网关返回 404」，说明 `*.zeabur.app` 前面可能还有一层 Zeabur 边缘网关，即「客户端 → 边缘 → 服务器入口 → 容器」两跳。那样 XFF 里会有两段，信任哪几跳必须实测
- 如果 API 域名有 AAAA 记录，办公室电脑可能走 IPv6 访问，服务端看到的是 IPv6 地址，和路由器 DDNS 上报的 IPv4 对不上（Q5）

如果推断成立，**今天就已经存在**、与白名单无关的后果：

- `audit_log.ip` 全是入口地址。PRD 要的「安全日志含 IP」现在记的就是错的，历史数据无法回补
- L3 `login:fail:{ip}:{username}` 退化成只按用户名计数：任何人对某个账号连错 5 次，这个账号 15 分钟内谁都登不上；连错 10 次直接锁号，要管理员解锁（L4）
- slowapi 默认限额按「端点 + 地址」分桶，地址全是同一个入口，于是全办公室在每个接口上共用每分钟 100 次。人多或页面并发请求多时可能被误限（这类 429 不写审计，只能看运行日志或 `/metrics`）
- 采集 Worker 的 IP 白名单同样只能看到入口地址，要么全拒、要么（填了入口地址）全放

#### 2.3 生产只读验证方法

- **方法 A（零部署，最快）**：跑第 4 节的 SQL-1、SQL-2，看登录类审计的 IP 分布。只有一两个地址、且落在 10.0.0.0/8、172.16.0.0/12、192.168.0.0/16、100.64.0.0/10 → 是入口地址；有多个公网地址、同一个人换地点会变 → 已经是真实 IP
- **方法 B（零部署）**：Zeabur → backend → 运行日志。uvicorn 访问日志每行开头就是它认定的客户端地址，看是不是私网段
- **方法 C（要部署，只读）**：管理员专用诊断端点。A、B 只能回答「现在拿到的是不是入口地址」，回答不了「转发链长什么样、该信任哪几跳」，配置信任必须靠它。方案（只写方案，不实施）：
  - `GET /api/security/ip-diagnostics`，要求 `security.ip_allowlist:write`（仅管理员），不写库
  - 返回：uvicorn 处理后的 `request.client.host`；原始 `X-Forwarded-For`、`X-Real-IP`、`Forwarded`、`X-Forwarded-Proto`、`Via`、`CF-Connecting-IP`；按当前信任配置解析出的客户端 IP 及其是否公网（`is_global`）；白名单做好后再加当前判定结果
  - 原始头只给管理员看：它会暴露内部网络结构
  - 实测三组：办公室网络、手机 4G，以及带伪造头的 `curl -H "X-Forwarded-For: 203.0.113.9" -H "X-Real-IP: 203.0.113.9" -H "Authorization: Bearer <管理员 token>" <API 域名>/api/security/ip-diagnostics`，再和 ip.sb 之类网站显示的出口 IP 对照
  - 端点不是一次性的：白名单设置页的「你当前的 IP / 是否命中」就用它

判读表：

| 观察到 | 含义 | 怎么配 |
|---|---|---|
| 对端是私网；XFF = `真实IP` 或 `伪造值, 真实IP` | 单跳入口，入口覆盖或追加 XFF | 信任入口网段，uvicorn 从右往左取第一个不可信地址，就是真实 IP |
| 对端私网；XFF = `伪造值, 真实IP, 某公网IP` | 前面还有一层（边缘网关） | 还要信任那一层的出口网段；网段没法枚举时，改为读「最外层代理覆盖写入、实测伪造值留不下来」的那个头（例如 `X-Real-IP`），在应用里自己取 |
| XFF 原样保留伪造值，且没有追加真实 IP | 入口透传 | XFF 不能用；找能覆盖写入的头，找不到则白名单方案不成立 |
| 对端已经是公网真实 IP | 入口做了源地址保留 | 不用配，现有代码就是对的 |

#### 2.4 取真实 IP 的正确做法

1. 只信任我们控制的那几跳代理，从 XFF 右往左取第一个不可信地址。uvicorn 在非 `*` 模式下就是这么做的（见 0.31.0 源码 `get_trusted_client_host`）
2. 升级 uvicorn 到 ≥0.31，建议锁 `uvicorn[standard]==0.32.0`（已从 [0.32.0 源码](https://raw.githubusercontent.com/encode/uvicorn/0.32.0/uvicorn/middleware/proxy_headers.py) 确认支持网段）。入口容器的地址会随重启变化，只能按网段信任，0.30.6 做不到
3. 用 Zeabur 环境变量 `FORWARDED_ALLOW_IPS=<实测网段>` 配（uvicorn 启动时自动读取），**不改启动命令**：生产启动命令是在 Zeabur 服务上单独配置的（`docs/ZEABUR_SETUP.md:67-73`、`deploy-secrets.local.md:130`），只改 Dockerfile 不一定生效
4. 永远不用 `*`：0.30.6 和 0.31+ 在 `*` 下都取 XFF 最左项，客户端随手就能伪造
5. 确认 backend 除了入口没有其他公网可达路径（Zeabur 上没给 8000 开 TCP 端口转发），否则有人直连并带伪造头。同项目内其他容器能直连，属于我们自己的容器，可以接受
6. 应用层再兜一道：白名单条目禁止私网 / 保留段；开启白名单时管理员 IP 必须是公网地址（Python `ipaddress` 的 `is_global`）。真实 IP 配置坏了时功能直接开不起来，而不是悄悄全放
7. 信任生效后 `X-Forwarded-Proto` 也会生效，`request.url.scheme` 变成 https。仓库里没有用 `request.url` / `url_for` 拼绝对地址的代码（grep 0 命中），影响可忽略
8. 修好之后 `audit_log.ip`、L3、slowapi、Worker 白名单一起变对，不需要另改代码；历史 `audit_log.ip` 无法回补

### D3 方案设计

#### 3.1 配置存在哪

- 现在的「系统设置」页（`frontend/src/pages/SettingsPage.tsx:23-66`）只对应企微一块：`GET/PUT /api/settings/wecom` 与 `POST /api/settings/wecom/test`（`features/settings/api.ts:24-54`）→ `wecom_config` 表（租户级单行，`backend/app/modules/wecom/models.py:35`），权限 `wecom.config:write`（`wecom/api.py:48-82`）。路由没有角色限制（`App.tsx:262`），菜单对所有人显示（`AppLayout.tsx:194`）。其他租户级配置在别的页面：催发阈值 `urge_config`（`urge/models.py:32`，单行 + 唯一索引 `uq_urge_config_tenant` `:77` + upsert `urge/repository.py:65-77`）
- **决定：租户级**，照 `urge_config` 的写法（`TenantScopedModel` + RLS + 单行 upsert）。理由：项目里所有业务配置都是租户级；生产只有 1 个租户（`docs/ZEABUR_SETUP.md:43`），租户级和全局今天没有差别，但租户级不用为「全局表」开特例。PRD 标题里的「全局安全」指对全员生效，不是指配置要跨租户
- 表草案（migration 057，编号以合并顺序为准）：
  - `login_ip_policy`：`enabled bool not null default false`、`updated_by`；唯一 `tenant_id`
  - `login_ip_entry`：`kind`（`ip` / `cidr` / `ddns`）、`value`（规范化后）、`note`、`expires_at`（非空即临时放行，只允许 ip / cidr）、`reason`、`is_active`、`created_by`；DDNS 专用列 `resolved_ips jsonb default '[]'`、`last_resolved_at`、`last_success_at`、`fail_count`、`last_error`
  - CHECK：kind 枚举；`expires_at IS NULL OR kind IN ('ip','cidr')`；临时放行必须有 `reason`。唯一：`(tenant_id, kind, value) WHERE expires_at IS NULL`。每个租户最多 32 条（与 Worker 白名单上限一致，`collect/schemas.py:21`）

#### 3.2 条目类型与校验

| 类型 | 示例 | 规则 |
|---|---|---|
| 单 IP | `1.2.3.4` / IPv6 | `ipaddress` 规范化（照 `collect/schemas.py:24-42`）；`::ffff:1.2.3.4` 归一成 IPv4；必须 `is_global`（拒绝私网、回环、链路本地、100.64.0.0/10 运营商级 NAT、保留段） |
| 网段 | `1.2.3.0/24` | `strict=False` 规范化；`is_global`；IPv4 前缀 ≥ /16、IPv6 ≥ /48，防手滑填出半个运营商 |
| DDNS 域名 | `office.example.com` | 主机名格式校验（不带协议、端口、通配符，不能是 IP 字面量）；第一期只解析 A 记录；解析结果同样要求 `is_global`，否则标错误「解析到非公网地址，可能是运营商级 NAT」 |

匹配逻辑把 `collect/worker_token_service.py:42-54` 的 `ip_is_allowed` 提到 `backend/app/core/security/` 下共用，补上 IPv4-mapped 归一，Worker 白名单改为引用它。

#### 3.3 DDNS 解析任务

- 新任务 `app.tasks.security_tasks.resolve_login_ip_ddns`：beat `crontab(minute="*/5")`、`queue="default"`，并登记进 `autodiscover_tasks`（`celery_app.py:55` 起）。租户列表取 `tenant` 表（照 `app/tasks/urge_tasks.py` 的 `_scan_all`）。**开关关着也照常解析**：业务要先看到解析正常才决定开启
- 解析用标准库 `loop.getaddrinfo(host, None, family=AF_INET)` 加 5 秒超时，不新增依赖
- **结果存库，不存 Redis**：Redis 是缓存库，重启或被驱逐就丢，丢了等于全员被拒；设置页要展示解析结果和时间；登录很少，每次多一条小查询可以忽略；应急时一条 SQL 就能改
- **失败处理**：保留上次成功的 `resolved_ips` 继续生效，`fail_count + 1`、记 `last_error`；连续 3 次（约 15 分钟）由正常转失败时，给管理员发一条站内通知（`NotificationType.SYSTEM`，`wecom/enums.py:39`；写法照 `worker_token_service.py:118-150`），恢复时再发一条。只保留、不过期：清空等于全员登不进
- 解析结果变化写审计 `security.ip_allowlist.ddns_changed`（变化前后的 IP），留下「哪段时间信任过哪个 IP」的记录
- **建议加**：登录未命中、且有 DDNS 条目超过 60 秒没解析过，就当场重解析一次（2 秒超时，按租户用 Redis `SET NX EX 60` 节流）。办公室换 IP 后的拒绝窗口从「路由器上报 + DNS TTL + 最多 5 分钟」缩到「路由器上报 + DNS TTL」；Worker 只有 2 个并发、可能被备份或汇总刷新占着时也不受影响
- 坑：新 model 模块要显式 import `app.modules.auth.models`（FK 指向 tenant / user），否则只在 Celery 链路上炸（台账「踩过的坑」）

#### 3.4 登录校验的位置与顺序

```
POST /api/auth/login
 0  取 IP：request.client.host（D-01 修好后才可信）
 1  L3 限流检查                         现状不动（service.py:115-128）
 2  按用户名查用户（跨租户，现状）       定租户：user.tenant_id；查不到用 default 租户
 3  读该租户策略 + 环境变量总开关，判定是否命中
 4  生效中且未命中：审计 login_ip_denied，返回 403 LOGIN_IP_NOT_ALLOWED
      不跑 bcrypt、不加 L3 / L4 失败计数、不看锁定 / 禁用
 5  锁定 / 禁用 / 验密码 / 成功          现状不动；成功审计带上判定结果（试运行用）
```

为什么放在「查用户之后、验密码之前」：

- 先验密码再验 IP 的话，名单外的人能用「403 IP 不符」和「401 密码错」分辨密码对不对，等于给外网开了一个验证密码的口子
- 名单外的请求不计失败数。现在外网任何人对一个账号连错 10 次就能把它锁死（L4，`service.py:237-261`）；改完之后名单外做不到
- 名单外所有分支（账号不存在 / 锁定 / 禁用 / 密码错）同一个响应、都不跑 bcrypt，没有存在性或耗时上的差别
- 要先查用户，是因为得知道用哪个租户的名单；查用户本身不产生可区分的响应
- 安全日志照 PRD 写「IP、时间、账号」，字段见 D4

拒绝文案照 PRD：「仅支持办公室网络访问，如需远程联系管理员开通」，建议后面带上「（你的 IP：x.x.x.x）」（Q2），远程员工报给管理员就能临时放行。

#### 3.5 后台开关与环境变量总开关的优先级

| `LOGIN_IP_ALLOWLIST_ENABLED`（环境变量） | 后台开关 | 行为 |
|---|---|---|
| false | 任意 | 不拦截；照样计算并记录是否命中；设置页显示「已被环境变量总开关停用」 |
| true（默认） | 关（默认） | 不拦截；记录是否命中（试运行） |
| true | 开 | 拦截 |

环境变量是最后兜底，命名照 `REPORT_SUMMARY_READS_ENABLED` 的先例（`core/config.py:161`）。策略每次登录 / 刷新现读库，不做进程内缓存，后台开关改了立即生效。

#### 3.6 管理员临时放行

- 管理员加一条 ip / cidr + 到期时间（默认 24 小时，最长 7 天）+ 原因（必填）；审计 `security.ip_allowlist.bypass_create`，可提前撤销（`bypass_revoke`）
- 判定时过滤 `expires_at > now()`，到期自动失效，不需要定时清理；过期条目留着做记录，列表显示「已过期」
- 按 IP 放行、不按账号放行，与「不设免检账号」一致；管理员出差前要先给自己加一条

#### 3.7 开启时的防自锁

1. 开启的前提：有效条目非空（未过期的 ip / cidr，或至少成功解析过一次的 ddns）。保存时对新增或改过的 DDNS 条目同步解析一次
2. 开启、以及开启状态下任何条目的增删改：用本次请求的真实 IP 跑一遍新配置，不命中就 422 `IP_ALLOWLIST_SELF_LOCKOUT`「保存后你（x.x.x.x）将无法登录」
3. 管理员 IP 不是公网地址（`is_global` 为假）时拒绝开启：「系统识别到的是内网地址，真实 IP 配置未生效」，防止 D-01 没修好时误开
4. 只靠临时放行命中时允许保存，但弹窗提醒「到期后你将无法从当前网络登录」
5. 条目本身禁止私网 / 保留段与过宽网段（3.2）

#### 3.8 多租户下怎么确定租户

- 登录时只有用户名，现在靠跨租户按用户名查人（`auth/repository.py:40-43`，bypass 会话）。白名单沿用：查到用户就用 `user.tenant_id` 的策略；查不到用 `code='default'` 的租户（与 `_ensure_initial_admin` 同一约定，`main.py:317-319`）。生产只有 1 个租户，两者一致，不会因为账号存不存在而响应不同
- 将来真有多个策略不同的租户时，「查不到就用 default」会留下侧信道（能分辨账号在不在某个没开白名单的租户）。到那时应改成登录时带租户码或按域名定租户，现在不必做
- 登录类审计要**显式**写 `tenant_id`：`AuditService.log` 现在只从上下文取（`core/audit.py:67`），而登录请求的上下文要么为空，要么来自前端顺手带上的旧 Bearer，`TenancyContextMiddleware` 不验签就写上下文（`backend/app/core/middleware/tenancy.py` 的 `dispatch`）。给 `log()` 加一个可选 `tenant_id` 参数
- 登录走 bypass 会话，ORM 租户过滤和 RLS 都不生效，读策略的查询要显式带 `tenant_id`

#### 3.9 应急预案（写进 `docs/ZEABUR_SETUP.md`）

1. 首选：管理员在设置页关掉，立即生效
2. 管理员也登不进：PG 控制台用超级用户执行 `UPDATE login_ip_policy SET enabled = false, updated_at = now();`，立即生效
3. 连库都不方便：Zeabur 上把 backend 的 `LOGIN_IP_ALLOWLIST_ENABLED` 改成 `false` 并重启（几分钟）

### D4 安全日志

现在没有独立的安全日志，`audit_log` 就是它：append-only（`002_u01_enable_rls.py:64` 对 `clothing_app` REVOKE UPDATE / DELETE），在线保留 `AUDIT_RETAIN_MONTHS`=12 个月，之后每月归档到 R2（`backend/app/tasks/cleanup_tasks.py` 的 `archive_audit_logs`）。PRD 的「写入安全日志」直接写这张表即可。

**登录类审计现在记了什么**

| action | 场景 | 账号信息 | ip | tenant_id |
|---|---|---|---|---|
| `login` | 成功 | `user_id` | 入口地址（D-01） | 推断为空 |
| `login_failed` | 用户不存在 | `after.username` | 同上 | 推断为空 |
| `login_failed` | 密码错 | `user_id` | 同上 | 推断为空 |
| `login_locked` / `login_disabled` | 已锁 / 禁用 | `user_id` | 同上 | 推断为空 |
| `login_rate_limited` | L3 超限 | **无** | 同上 | 推断为空 |
| `user_lock` | 累计 10 次锁号 | `user_id` | 同上 | 推断为空 |

「推断为空」的依据见 3.8；用 SQL-5 核对。

**被拦截的登录写什么**（新 action `login_ip_denied`）

- 时间：`created_at`
- IP：D-01 修好后的真实 IP（`ip` 列）
- 账号：`after.username`（用户输入的原样，截断到 64 字）；查到用户时另写 `user_id` / `resource_id`
- 原因：`purpose = 'ip_not_allowed'`；`after.ip_allowlist = {enforced, matched, entries, ddns_last_success_at}`
- 租户：显式 `tenant_id`
- 其余照旧：`user_agent`、`request_id`

同时把现有登录类 action 补齐：都写 `after.username` 和显式 `tenant_id`，`login_rate_limited` 补上账号；成功的 `login` 带 `after.ip_allowlist`（试运行依据）。刷新被拒写 `token_refresh_ip_denied`。配置变更：`security.ip_allowlist.policy_update`、`entry_create` / `entry_update` / `entry_delete`、`bypass_create` / `bypass_revoke`、`ddns_changed`。

**谁能看**

- 现有 `GET /api/audit-logs`（`auth/api.py:383-388`）要 `auth.audit:read`（`auth/permissions.py:21`）。默认只有持 `*` 的 admin / platform_admin 有（`auth/default_roles.py:94-105`），没有角色持 `auth.*`。但 `has()` 的前缀通配只看第一段（`core/security/permissions.py:50-51`），以后谁被授了 `auth.*:read` 或 `auth.*:*`，就能读全部审计。上线前跑 SQL-6 看有没有这样的覆盖授权
- 这个端点不按租户过滤（`AuditLog` 继承 `Base` 而非 `TenantScopedModel`，`auth/models.py:233`，ORM 租户过滤不作用于它；`audit_log` 也没开 RLS）。单租户下不构成泄露，但多租户时会看到别家的审计
- 建议：新端点 `GET /api/security/login-logs`，只查登录类 action、显式按当前租户过滤（现有索引 `ix_audit_log_tenant_action_created` 正好支持），筛选项：时间、结果（拒绝 / 失败 / 试运行未命中）、账号、IP。闸门沿用 `auth.audit:read`：同一份数据用同一个闸门，与 4b-2 金额时间线的做法一致
- 白名单管理用新 scope `security.ip_allowlist:write`（读写共用一个，照 `wecom.config:write` 的先例）。不叫 `auth.ip_allowlist:write`：将来谁拿到 `auth.*:write`（比如为了管用户），会顺带拿到白名单控制权。`security` 是新一级域，覆盖授权要求 scope 先存在于 `permission` 表（`PermissionService._resolve_permission_id`），所以不可能有人已经持有 `security.*`

### D5 管登录还是也管会话

PRD 改动 7 原文只说「登录时校验当前出口 IP」「不在白名单 → 拒绝登录」，没有提已登录的会话（PRD docx「改动 7」节）。

现状：access token 30 分钟（`core/config.py:67`），refresh 7 天且每次刷新换发新的 7 天（滑动续期，`auth/service.py:279-302`），前端遇 401 自动刷新（`apiClient.ts:69-119`）。只要 7 天内用过一次，会话就永不过期。

| 方案 | 离开办公室后还能用多久 | 改动 | 副作用 |
|---|---|---|---|
| 只管登录 | 无限（7 天内用过就一直续） | 无 | 在办公室登录、把电脑带回家就能一直用，白名单形同虚设 |
| **登录 + 刷新时校验（推荐）** | ≤30 分钟（access token 有效期） | 刷新端点接收 `Request` 取 IP，按 `user.tenant_id` 判定；不通过就吊销该 jti、审计 `token_refresh_ip_denied`、返回 403 `LOGIN_IP_NOT_ALLOWED`；新 refresh 行补记 ip / UA；前端把原因带到登录页 | 办公室换 IP 的窗口期内，在线的人会在 30 分钟内陆续掉线，且要等 DDNS 解析到新 IP 才能登回来 |
| 每个请求都校验 | 立即 | 在 `get_current_user`（`auth/deps.py:34-66`）里每请求判定，需要缓存策略 | 每请求开销；换 IP 时立刻全员 403；改动面最大 |

推荐第二种，列为 Q1。办公室换 IP 后的窗口 = 路由器 DDNS 上报延迟 + DNS TTL + 我们的解析周期（5 分钟；做了 3.3 的「未命中即时重解析」后约等于 0）。前两项取决于路由器和 DDNS 服务商，建议业务重拨一次路由器实测。

前端配合：`apiClient.ts:114-119` 刷新失败时只清 token 并派发 `auth:unauthorized`，原因会丢。要在 catch 里识别 `LOGIN_IP_NOT_ALLOWED`，把提示存进 `sessionStorage`，登录页读出来显示。

## 3. 需业务确认的问题

| # | 问题 | 推荐默认答案 | 理由 |
|---|---|---|---|
| Q1 | 只管登录，还是刷新 token 时也校验（D5） | 登录 + 刷新时校验 | 只管登录的话，办公室登录的会话带回家能靠 7 天滑动续期一直用；刷新校验让离开办公室后最多 30 分钟失效，改动小 |
| Q2 | 拒绝提示里要不要带上用户当前的 IP | 带：「仅支持办公室网络访问，如需远程联系管理员开通（你的 IP：x.x.x.x）」 | 远程员工得把 IP 报给管理员才能临时放行；IP 是用户自己的，不泄密 |
| Q3 | 临时放行谁能开、最长多久 | 仅管理员；默认 24 小时，最长 7 天，必填原因 | PRD 只说「联系管理员开通」；设上限避免临时放行变成常驻例外 |
| Q4 | 开关关闭期间要不要「试运行」：照样记录每次登录是否命中，设置页显示最近 7 天的命中情况 | 要；开启前至少观察 3 个工作日，确认办公室登录全部命中再开 | 业务定的是「确认解析正常后再开启」，这一步需要证据，不能只看域名解析出来的 IP 对不对 |
| Q5 | 如果 API 域名有 AAAA 记录、办公室电脑走 IPv6 访问怎么办 | 先生产核对（第 4 节）。第一期只匹配 IPv4；诊断页若显示办公室走的是 IPv6，再定「让 API 只走 IPv4」还是「支持 IPv6 前缀条目」 | 路由器 DDNS 通常只报 IPv4；IPv6 下每台电脑地址都不同，前缀也会变，不是加一条记录能解决的 |
| Q6 | 办公室路由器 WAN 口是不是公网 IP（台账待确认第 1 条的补充） | 业务在路由器管理页看 WAN 口地址，和 ip.sb 显示的出口 IP 对照：一致才可行 | 运营商级 NAT 下 WAN 口是 100.64.x.x 之类的内网地址，路由器 DDNS 报上去的地址永远匹配不上；换成「外部探测上报」的 DDNS 客户端能用，但出口 IP 与同一运营商的其他用户共享 |

## 4. 迁移与存量数据影响

- **migration 057**（编号以合并顺序为准，其他分支可能也要占号）：新建 `login_ip_policy`、`login_ip_entry`，带 RLS（`enable_rls_sql`）、CHECK、索引；权限 seed `security.ip_allowlist:write`，照 `052_report_summary_tables.py:245-266` 的写法插 `permission` 并只给 admin 建 `role_permission`（admin / platform_admin 的 `*` 本来也覆盖）
- **不改已有表**；不动汇总表，**不需要**清 `report_summary_coverage`
- **存量影响为零**：功能默认关闭，上线后登录行为不变（只多记录试运行字段）
- 历史 `audit_log.ip` / `tenant_id` 不回补：`audit_log` 是 append-only（`002_u01_enable_rls.py:64`），而且旧的 IP 本身就是入口地址，补不出来
- 配置变更：Zeabur backend 新增环境变量 `FORWARDED_ALLOW_IPS`（值由诊断结果定）；`LOGIN_IP_ALLOWLIST_ENABLED` 可不配（默认 true）；`backend/requirements.txt:8` uvicorn 升级。`.env.example` 与 `docs/ZEABUR_SETUP.md` 同步

### 需要生产核对的只读 SQL

在 PG 控制台用超级用户执行：`user`、`refresh_token`、`user_permission_override` 开了 FORCE RLS（`002_u01_enable_rls.py:29-34`），普通应用角色查不到行。

```sql
-- SQL-1 登录类审计的 IP 分布：只有一两个私网地址 = 拿到的是入口地址
SELECT ip,
       count(*)                AS n,
       count(DISTINCT user_id) AS users,
       min(created_at)         AS first_seen,
       max(created_at)         AS last_seen
FROM audit_log
WHERE action IN ('login','login_failed','login_locked','login_disabled',
                 'login_rate_limited','user_lock')
  AND created_at >= now() - interval '30 days'
GROUP BY ip
ORDER BY n DESC
LIMIT 50;

-- SQL-2 按地址类型归类（只取 IPv4 形态的值，避免 cast 报错；IPv6 看 SQL-1 原值）
WITH t AS MATERIALIZED (
  SELECT ip::inet AS addr
  FROM audit_log
  WHERE action LIKE 'login%'
    AND created_at >= now() - interval '90 days'
    AND ip ~ '^([0-9]{1,3}\.){3}[0-9]{1,3}$'
)
SELECT CASE
         WHEN addr << inet '10.0.0.0/8' OR addr << inet '172.16.0.0/12'
           OR addr << inet '192.168.0.0/16' THEN '私网 RFC1918'
         WHEN addr << inet '100.64.0.0/10' THEN '运营商级 NAT'
         WHEN addr << inet '127.0.0.0/8'   THEN '回环'
         ELSE '公网'
       END AS kind,
       count(*)             AS n,
       count(DISTINCT addr) AS distinct_addrs
FROM t
GROUP BY 1
ORDER BY n DESC;

-- SQL-3 refresh_token 的 IP（登录签发的有值，刷新换发的为空）
SELECT ip, count(*) AS n, max(issued_at) AS last_issued
FROM refresh_token
WHERE issued_at >= now() - interval '30 days'
GROUP BY ip
ORDER BY n DESC
LIMIT 20;

-- SQL-4 L3 限流与锁号的历史（入口地址下 L3 等于按账号计数）
SELECT created_at::date AS d, action, count(*) AS n
FROM audit_log
WHERE action IN ('login_rate_limited', 'user_lock')
GROUP BY 1, 2
ORDER BY 1 DESC
LIMIT 60;

-- SQL-5 登录类审计的 tenant_id 是否为空（验证 3.8 的推断）
SELECT action,
       count(*)                                  AS n,
       count(*) FILTER (WHERE tenant_id IS NULL) AS null_tenant
FROM audit_log
WHERE action LIKE 'login%' OR action = 'user_lock'
GROUP BY action
ORDER BY n DESC;

-- SQL-6 通配权限与 auth / security 相关授权的持有者（新 scope 起名前必查）
SELECT p.scope, r.code AS role
FROM permission p
JOIN role_permission rp ON rp.permission_id = p.id
JOIN role r ON r.id = rp.role_id
WHERE p.scope LIKE '%*%'
ORDER BY p.scope, r.code;

SELECT u.username, p.scope, o.effect
FROM user_permission_override o
JOIN permission p ON p.id = o.permission_id
JOIN "user" u ON u.id = o.user_id
WHERE p.scope LIKE '%*%' OR p.scope LIKE 'auth.%' OR p.scope LIKE 'security.%'
ORDER BY u.username, p.scope;

-- SQL-7 租户数，以及跨租户重名用户名（3.8 的前提；见旁支发现 3）
SELECT id, code, status FROM tenant WHERE deleted_at IS NULL;

SELECT username, count(*) AS n
FROM "user"
WHERE deleted_at IS NULL
GROUP BY username
HAVING count(*) > 1;

-- SQL-8 最近 30 天登录量：估审计增量；刷新校验的额外开销约为活跃用户 × 每 30 分钟一次
SELECT action, count(*) AS n, count(DISTINCT user_id) AS users
FROM audit_log
WHERE created_at >= now() - interval '30 days'
  AND (action LIKE 'login%' OR action IN ('user_lock', 'logout'))
GROUP BY action
ORDER BY n DESC;
```

### 需要生产核对的非 SQL 项（都只读）

1. Zeabur backend 的环境变量里有没有 `FORWARDED_ALLOW_IPS`（只看不改）
2. Zeabur backend 运行日志里 uvicorn 访问日志行开头的客户端地址
3. Zeabur backend 的 Networking 有没有给 8000 开公网 TCP 端口
4. `nslookup -type=AAAA clothinkai-api.zeabur.app`（以及将来启用的 `api.clothinkai.com`）有没有 AAAA 记录
5. 办公室：路由器 WAN 口地址 vs ip.sb 显示的出口 IP；DDNS 域名的 `nslookup` 结果 vs 出口 IP；重拨一次路由器，记下 DDNS 多久更新

## 5. 建议的实施顺序

1. **生产核对**（编排方，约 0.5 天）：第 4 节的 SQL 与非 SQL 项。SQL-1 / SQL-2 如果显示已经是公网真实 IP，第 3 步可以省掉
2. **PR-a 诊断端点**（S，只读、不改任何行为）：`GET /api/security/ip-diagnostics` + 共享 IP 工具。部署后做办公室 / 4G / 伪造头三组实测，按 2.3 的判读表定信任配置
3. **PR-b 真实 IP**（S）：升级 uvicorn，Zeabur 配 `FORWARDED_ALLOW_IPS`，加代理头行为测试；再用诊断端点复测（伪造头必须无效）。顺带补上 L2 登录限流。这一步本身就修好了 `audit_log.ip`、L3、slowapi 和 Worker 白名单
4. **PR-c 白名单后端**（L）：057 + service + 登录接入 + 审计补齐 + 总开关 + DDNS 任务 + 安全日志端点。默认关闭，上线后行为不变
5. **PR-d 前端**（M）：设置页两个 Tab、登录与刷新的拒绝提示
6. **刷新校验**（S，待 Q1）：可以并进 PR-c，用同一个判定函数和同一组开关
7. **上线流程**：业务在设置页填 DDNS 域名 → 看解析状态 → 试运行观察 ≥3 个工作日（Q4）→ 管理员开启 → 观察一周安全日志

依赖：第 4 步依赖第 3 步（防自锁的「管理员 IP 必须是公网」在 IP 没修好时会直接拒绝开启，功能等于不可用）。与其他分支的交集只有 migration 编号和权限 seed（批次 6 的导出权限也要新加 scope），由编排方排号。

## 6. 实施清单

| 类别 | 编号 | 内容 | 主要文件 | 工作量 |
|---|---|---|---|---|
| 后端 | B1 | 诊断端点 `GET /api/security/ip-diagnostics`（admin，只读，返回对端地址与原始转发头） | 新 `backend/app/modules/security/api.py`；`main.py` 注册路由 | S |
| 后端 | B2 | 真实 IP：`uvicorn[standard]==0.32.0`；Zeabur `FORWARDED_ALLOW_IPS`；共享工具 `core/security/client_ip.py`（`ip_is_allowed` 从 collect 提上来，加 IPv4-mapped 归一与 `is_global`），Worker 白名单改为引用 | `requirements.txt:8`、`collect/worker_token_service.py:42-54` | S |
| 后端 | B3 | L2 登录限流 `@limiter.limit(settings.RATE_LIMIT_LOGIN_IP)`；`limiter` 挪到 `core/rate_limit.py`，解掉 `main.py` 与 `auth/api.py` 的循环 import | `auth/api.py:69`、`main.py:82-87` | S |
| 后端 | B4 | 模型 / schema / repository / service：策略读取与判定、条目 CRUD 与校验（3.2）、防自锁（3.7）、临时放行（3.6）、保存时同步解析 DDNS | 新 `backend/app/modules/security/` | M |
| 后端 | B5 | 登录接入（3.4）：查用户定租户 → 判定 → 403 且不计失败数；`AuditService.log` 加可选 `tenant_id`；登录类审计补齐账号 / 原因 / 租户；成功审计带判定结果 | `auth/service.py:101-230`、`core/audit.py:44-90`、`core/exceptions.py` | M |
| 后端 | B6 | 刷新接入（待 Q1）：刷新端点收 `Request`，判定，拒绝时吊销 jti 并审计；新 refresh 行补记 ip / UA | `auth/api.py:89-101`、`auth/service.py:266-304` | S |
| 后端 | B7 | 总开关 `LOGIN_IP_ALLOWLIST_ENABLED: bool = True` | `core/config.py`、`.env.example` | S |
| 后端 | B8 | 安全日志端点 `GET /api/security/login-logs`（`auth.audit:read`，显式租户过滤，含「试运行未命中」筛选） | `security/api.py` | S |
| 后端 | B9 | （可选）`GET /api/audit-logs` 补租户过滤 | `auth/repository.py:276` 起 | S |
| 迁移 | M1 | 057：两张表 + RLS + CHECK + 索引 + 权限 seed；downgrade 删表与权限行 | 新 `backend/alembic/versions/057_*.py` | S |
| Beat | T1 | `security_tasks.resolve_login_ip_ddns`：`crontab(minute="*/5")`、`queue="default"`；遍历 `tenant` 表；失败沿用 + 连续 3 次通知；结果变化写审计；登记 `autodiscover_tasks`；model 模块显式 import `auth.models` | 新 `backend/app/tasks/security_tasks.py`、`core/celery_app.py:55`、`:73` | M |
| Beat | T2 | （建议）登录未命中时节流重解析（Redis `SET NX EX 60`） | `security/service.py` | S |
| 前端 | F1 | `SettingsPage` 改成 Tabs：企业微信 / 登录 IP 白名单 / 登录安全日志；后两个仅 admin、platform_admin 可见（照 `AppLayout.tsx:34-36` 的角色判断，后端 403 兜底） | `pages/SettingsPage.tsx`、新 `features/security/api.ts` | S |
| 前端 | F2 | 白名单面板：状态卡（后台开关、环境变量总开关、你的 IP、是否命中、试运行统计）；条目表（类型、值、解析结果、最后解析时间、状态、到期）；新增 / 编辑 / 删除；「立即解析」；开启确认弹窗（显示自检结果）；临时放行表单 | 新 `features/security/components/*` | M |
| 前端 | F3 | 安全日志面板：时间 / 账号 / IP / 结果 / 原因 / UA，可筛选 | 同上 | S |
| 前端 | F4 | 刷新被拒时把原因带到登录页（`sessionStorage`），登录页用 Alert 显示 | `services/apiClient.ts:114-119`、`pages/LoginPage.tsx` | S |
| 文档 | W1 | `ZEABUR_SETUP.md` 加「真实 IP 配置」与「白名单应急」（3.9 三步）；`.env.example` 加两个变量 | `docs/ZEABUR_SETUP.md`、`.env.example` | S |

**测试要点**（照台账做法：每组测试写完都要做一次故障注入，确认会红）

- 单元（`tests/unit/`）：条目解析 / 规范化 / 校验（私网、保留段、100.64/10、过宽网段、非法域名、IPv4-mapped）；匹配；过期过滤；开关真值表（3.5）
- 集成（沿用 `tests/integration/test_auth_login.py` 的 `stub_cache` 写法）：名单外 → 403 且不计失败数（连续 20 次也不锁号）；不存在 / 锁定 / 禁用 / 密码错的账号从名单外登录，响应完全一致；审计含 IP / 时间 / 账号 / 原因 / `tenant_id`；后台开关关闭时照常登录、审计记 `matched=false`；总开关关闭时不拦截；刷新被拒且 jti 被吊销；防自锁（名单外开启、只有未解析的 DDNS、私网条目、管理员 IP 为私网 → 422）
- DDNS 任务：假 resolver；成功写入、失败沿用、连续失败只通知一次、私网结果拒用、变化写审计、多租户互不影响
- 代理头：用 uvicorn `ProxyHeadersMiddleware` 包住 app，配合 httpx `ASGITransport(client=(...))` 模拟对端：可信对端 + `伪造值, 真实IP` → 取到真实 IP；不可信对端 → 忽略 XFF。故障注入：信任配置改成 `*` 时测试必须红
- 权限：非 admin 访问白名单与安全日志接口 → 403；只持 `auth.*:*` 的用户拿不到 `security.ip_allowlist:write`
- CI：`ruff check .`、`ruff format --check .`、`mypy app`、`pytest`（`.github/workflows/ci.yml`）；前端 `npm run type-check`、`npm run build`，并按台账习惯在 375 / 768 / 1440 宽度实测

## 7. 旁支发现（不在本分支范围，供编排方取舍）

1. **`/metrics` 无鉴权对外暴露**（`main.py:420-423`）：接口路径与请求计数谁都能看。风险低，建议加鉴权或只在内网暴露
2. **登录请求的租户上下文来自未验签的 token**：`TenancyContextMiddleware` 用 `decode_token_unverified` 写 `tenant_id_ctx`，token 里 `actor_type=platform_admin` 时还会把 `bypass_rls_ctx` 置 True（`core/middleware/tenancy.py` 的 `dispatch`）。需要鉴权的端点会在 `get_current_user` 验签时 401，不受影响；对不经鉴权、又用 `SessionDep` 的端点有什么影响，本次**未验证**，建议单独排查
3. **跨租户重名用户名会让登录 500**：用户名只在租户内唯一（`auth/models.py:137`），`get_by_username` 跨租户查、用 `scalar_one_or_none()`（`auth/repository.py:40-43`），两个租户有同名用户时抛 `MultipleResultsFound`。单租户下不会发生（SQL-7 可核对）
4. **账号存在性侧信道**（现状）：锁定 / 禁用在验密码之前判断并返回不同的码（`auth/service.py:147-171`）；不存在的账号不跑 bcrypt，响应更快。白名单开启后只对名单外 IP 消失；办公室内照旧。要改会改变用户看到的提示，需业务同意，本分支不建议动
5. **`GET /api/audit-logs` 不按租户过滤**：见 D4，单租户下不构成泄露

## 参考

- [uvicorn 0.30.6 `proxy_headers.py`](https://raw.githubusercontent.com/encode/uvicorn/0.30.6/uvicorn/middleware/proxy_headers.py)：信任集合按字符串精确匹配；`*` 时取 XFF 最左项
- [uvicorn 0.30.6 `config.py`](https://raw.githubusercontent.com/encode/uvicorn/0.30.6/uvicorn/config.py)：`FORWARDED_ALLOW_IPS` 未设时默认 `127.0.0.1`
- [uvicorn 0.31.0 `proxy_headers.py`](https://raw.githubusercontent.com/encode/uvicorn/0.31.0/uvicorn/middleware/proxy_headers.py)、[0.32.0](https://raw.githubusercontent.com/encode/uvicorn/0.32.0/uvicorn/middleware/proxy_headers.py)：新增 `_TrustedHosts`，支持网段，从右往左取第一个不可信地址
- [slowapi 0.1.9 `util.py`](https://raw.githubusercontent.com/laurentS/slowapi/v0.1.9/slowapi/util.py)：`get_remote_address` 即 `request.client.host`
- [slowapi 0.1.9 `extension.py`](https://raw.githubusercontent.com/laurentS/slowapi/v0.1.9/slowapi/extension.py)：默认限额按端点分桶
- [Zeabur Caddy 模板](https://zeabur.com/templates/FFDLWU)：入口代理会加 `X-Forwarded-For` / `X-Real-IP`，建议按私网段信任
- [Zeabur 高可用架构文档](https://zeabur.com/docs/en-US/networking/high-availability)：经 Zeabur 入口时可从标准转发头拿到真实 IP

> 外部内容均为转述，未大段引用（Content was rephrased for compliance with licensing restrictions）。
