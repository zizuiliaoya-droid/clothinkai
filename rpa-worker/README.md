# RPA 采集 Worker（占位）

> 此目录在 U13（V1 阶段）启用，用于实现千牛 / 万相台 / 灰豚的自动数据采集。
> 本目录为外部独立部署的 Worker（Windows VM 或 Docker 主机），通过 HTTPS pull 主系统的采集任务 API。
>
> U01 阶段不提供实现，仅占位。

## 通信协议（U13 实施时参考）

- `POST /api/crawler/tasks/poll` — Worker 拉取下一个 pending 任务，主系统返回任务详情 + 一次性凭据令牌
- `POST /api/crawler/tasks/{id}/exchange` — 用一次性凭据令牌换取明文密码（5 分钟 TTL）
- `POST /api/crawler/tasks/{id}/result` — Worker 完成后上传 CSV/Excel 文件 + 状态

详见 `aidlc-docs/inception/application-design/unit-of-work.md` 第 2.2.1 节凭据安全边界。
