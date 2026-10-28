# 本轮 067 → 068 → 069 发布适配

此目录沿用单步 HostCoordinator 和现有离线打包工具。此文件是本轮准备说明，不是执行授权，也不证明已经部署。

## 已固定的变化

- 起点 `067_retro_completion`，终点 `069_remove_unused_shop_caches`，中间必须为 `068_promotion_content_platform`。新启动许可只允许 069。
- 新冻结清单：`../phase1/implementation-pins-069-1028.json`；原 `implementation-pins.json`、历史许可、回执和 HOLD 不修改。
- 现役 compose：`/srv/clothinkai/production-20261018/releases/flow78-78e95a95ed7c/compose.json`，SHA256 `9724478e7e5cd2f7c8b0738d250e04c49609990a69acfcb54d9111e18cea6059`。
- 现役入口：同一 release 的 `web-control/nginx-active.conf`，SHA256 `89519863caaa05ed9e7e314bf7f912db240e2a8371ee26208c64579d98e05a93`。关闭入口改这一目录；新 Web 则挂新 release 的独立 web-control。
- 根目录 `compose.json` 是旧 066 配方，不能拿来启动或恢复当前业务。API/worker/beat 的 `/opt/update` 和 `/run/release-guard` 仅替换为新 release，不追加重复挂载；原 `/opt/production` 运维脚本挂载仍保留。
- 验收保留旧推广、复盘、完结、个人授权、角色授权、财务集合，以及三张日汇总、覆盖记录、店铺手填数据；只允许新增分类列/平台表，删除两张周/月缓存。核对分类全量回填、平台表列/约束/索引/owner/RLS/ACL。新平台表开站前应为空。
- 068/069 不修改权限。验收直接核对权限集合未变化，不沿用 067 新增 scope 的五分钟缓存等待；仍核对两次运行稳定状态。beat PID 1 修复保留。
- 已退役 Zeabur 不属于本轮检查范围，不生成“已冻结旧站”的假证据。

## 本地与 CI

本地仅运行 `ci_tests/test_release_069_contract.py`（标准库 unittest）。专用 `.github/workflows/release-069-validation.yml` 仅由 `codex/release-1007-069-1028` push 触发，无生产凭据/部署步骤，使用临时 PostgreSQL 18.6、真实非 superuser 的 clothing_bypass owner 和 clothing_app RLS 角色。

CI 产品输入暂固定候选集成 `cbb1d455ab017850baa52f3ac7ac7ecd1652a771`，不是已合并声明。真实 067→069、四类分类回填、两租户平台表读写隔离、旧数据保留及五个负向验收在该通道验证；不能以本地静态检查替代。

## 之后的执行输入

产品最终 M 和 CI 结果确认后，使用 `../phase1/release_builder.py` 既有参数，在新目录导出 M、构建新前端并生成本轮 input pins/candidate/package evidence。可复用已有 Linux vendor 和服务器基础镜像。`start_067.py` / `migrate_067.py` 文件名与 `flow78-<M前12位>` 目录前缀是兼容命名，实际 schema 绑定已更新。

从旧单步配方新建本轮 assemble/issue-permit/run-one-step 输入；填本轮 M、目录、批准记录和执行前实际基线。迁移许可只能引用本轮真实 BACKUP/RESTORE 回执。不要运行 `release-inputs-1026`、旧 manual-forward 或原自动 HOLD 的入口。

MIGRATE 前如确需有限恢复，使用上述现役 067 compose、原镜像和原 067 guards；Web 恢复目标也是现役 release/web-control，内容来自本次保存的 `old-active.conf` 并核对上述 SHA。不得操作根目录旧 nginx/compose。恢复动作仍须依据本次现场状态与既有批准范围单独形成步骤；本适配不自动恢复。

一旦迁移已开始或新版本可能写入，不自动 downgrade；068 降级会丢新平台记录，069 降级只恢复空缓存表。保留本轮状态和备份，按实际情况修复或制定限定恢复。历史 23 步成功后的 HOLD 仍保持历史事实。
