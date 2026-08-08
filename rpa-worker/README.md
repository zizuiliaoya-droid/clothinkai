# RPA 采集 Worker

这是一个 Python 3.12 pull Worker：从主系统领取任务，以一次性 `cred_token` 交换凭据，执行管理员配置的本地采集命令，并上传真实、非空结果。它不内置千牛、万相台或灰豚的破解、逆向、验证码绕过或风控规避能力；平台命令必须由部署方提供已授权、合规的本地采集器。

## 配置

程序只读取进程环境，不自动加载 `.env`。

| 变量 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| `API_BASE_URL` | 是 | - | 后端根 URL；非 loopback 默认强制 HTTPS |
| `ALLOW_INSECURE_HTTP` | 否 | `false` | 仅可信开发网络可设 `true`，生产禁止 |
| `WORKER_TOKEN` | 是 | - | 后端签发的 Worker Token |
| `POLL_INTERVAL` | 否 | `10` | 空队列轮询秒数，必须大于 0 |
| `HTTP_TIMEOUT` | 否 | `30` | HTTP 超时秒数，必须大于 0 |
| `WORK_DIR` | 否 | `./work` | 每任务临时目录的父目录 |
| `QIANNIU_COMMAND` | 否 | 空 | 千牛本地命令 |
| `WANXIANGTAI_COMMAND` | 否 | 空 | 万相台本地命令 |
| `HUITUN_COMMAND` | 否 | 空 | 灰豚本地命令 |

命令来自管理员环境配置，不接受任务下发命令。Worker 使用 `shlex.split` 拆分参数并以 `shell=False` 启动，因此不支持管道、重定向或 shell 变量展开。未知平台及未配置平台会失败上报。

## Windows 启动

```powershell
cd e:\work\Pycharm_Projection\eCommerce_v4\rpa-worker
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
$env:API_BASE_URL = "https://api.example.com"
$env:WORKER_TOKEN = Read-Host "Worker Token"
$env:POLL_INTERVAL = "10"
$env:HTTP_TIMEOUT = "30"
$env:WORK_DIR = "$PWD\work"
$env:QIANNIU_COMMAND = 'python C:\rpa\qianniu_collector.py'
rpa-worker --once  # 最多领取并处理一个任务
rpa-worker         # 长期轮询，Ctrl+C 优雅停止
```

## Docker 启动

镜像仅包含 Worker。本地采集器及其运行时需挂载或通过派生镜像提供；不要把 Token 写进镜像。

```powershell
docker build -t ecommerce-rpa-worker:1.0.0 .
docker run --rm --stop-timeout 45 --env-file .env -v ${PWD}\work:/data/work ecommerce-rpa-worker:1.0.0 --once
docker run -d --restart unless-stopped --stop-timeout 45 --name rpa-worker --env-file .env `
  -v ${PWD}\work:/data/work ecommerce-rpa-worker:1.0.0
```

容器或服务管理器的优雅停止窗口必须大于 `HTTP_TIMEOUT`，建议至少为 `HTTP_TIMEOUT + 15s`；默认 `HTTP_TIMEOUT=30` 时使用 45 秒或更长，避免强制终止发生在结果回传或任务目录清理期间。

若采集依赖 Windows 客户端、桌面浏览器或交互式登录，应运行在受控 Windows VM，而不是无头 Linux 容器。

## 平台命令契约

三个命令分别对应后端平台值“千牛”“万相台”“灰豚”。每次执行时，采集器必须：

1. 从子进程环境读取小写变量 `username`、`password`、`target_date`、`output_path`；`target_date` 是 `YYYY-MM-DD`，`output_path` 是绝对 `.csv` 路径。
2. 不从命令行接收或记录密码；Worker 只向采集器传递最小运行环境和当前任务的四个字段，不继承 `WORKER_TOKEN`、数据库密码或云密钥，并丢弃采集器 stdout/stderr。
3. 成功时退出码为 0，并在 `output_path` 写入存在且非空的真实结果；失败时返回非 0。
4. 输出列和数据类型须符合后端导入模板。Worker 不伪造、不修补业务数据。

## HTTP 协议

所有请求携带 `X-Worker-Token`，不跟随重定向：

- `POST /api/crawler/tasks/poll`：200 返回任务；204 返回 `None`。
- `POST /api/crawler/tasks/{task_id}/exchange`：JSON `{"cred_token":"..."}`。
- `POST /api/crawler/tasks/{task_id}/result`：multipart；成功包含 `lease_token`、`status=success` 和 `file`，失败包含 `lease_token`、`status=failed` 和 `error`。`lease_token` 使用 poll 返回的 `cred_token`；任务回收后旧令牌会被 fencing 拒绝。

## 安全边界

- Worker Token、一次性凭据令牌、用户名和密码不写日志；密码仅在当前采集子进程环境中短暂存在。部署时使用独立低权限账户，并将 Token 放在 Secret 管理设施中。
- 命令必须由管理员配置为可信本地程序。任务不能改变可执行命令；`shell=True` 被禁止。
- 每个任务使用 `WORK_DIR` 下的独立临时目录，无论成功或失败都会尽力递归删除。部署方仍应使用加密磁盘、严格 ACL 和系统级残留清理策略。
- 非 loopback 地址默认强制 HTTPS；只有可信开发网络可显式设置 `ALLOW_INSECURE_HTTP=true`。生产使用 HTTPS、固定出口 IP 和后端 IP allowlist。签发 Token 时必须至少配置一个单 IP 或 CIDR（支持 IPv4/IPv6）；令牌泄露时应立即在后端吊销。
- 成功和失败结果仅对网络错误或服务端 5xx 自动重试最多 3 次；后端按租约与任务终态幂等处理。若成功回传响应不可确认，Worker 不会改报失败，避免覆盖已提交的成功结果。
- 采集命令超时或 Worker 收到 SIGINT/SIGTERM 后，会终止采集器及其后代进程，再清理任务目录，降低凭据继续驻留的风险；目录清理失败会记录不含敏感值的告警。
- 平台采集必须获得账户所有者授权，遵守平台条款、频率限制和适用法律。本 Worker 不提供验证码破解、反爬绕过、风控规避或访问控制绕过。
