-- 本地 docker-compose 用：在 PostgreSQL 容器初始化时执行（init.d 自动运行）。
-- 生产环境（Zeabur）需在 PG Web 控制台手动执行此脚本。
--
-- 创建 3 个数据库角色，配合 RLS 策略实现多租户隔离：
--   clothing_app      —— 业务主连接，启用 RLS
--   clothing_bypass   —— 系统任务（Celery beat / 备份 / system_context）+ platform_admin
--   clothing_archiver —— audit_log 归档专用（拥有 DELETE 权限）

-- ----------------------------------------------------------------------------
-- 角色创建（密码请在生产部署时通过 ALTER ROLE 替换）
-- ----------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clothing_app') THEN
        CREATE ROLE clothing_app NOINHERIT LOGIN PASSWORD 'app_password_change_me';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clothing_bypass') THEN
        CREATE ROLE clothing_bypass BYPASSRLS NOINHERIT LOGIN PASSWORD 'bypass_password_change_me';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clothing_archiver') THEN
        CREATE ROLE clothing_archiver NOINHERIT LOGIN PASSWORD 'archiver_password_change_me';
    END IF;
END
$$;

-- ----------------------------------------------------------------------------
-- 数据库连接权限
-- ----------------------------------------------------------------------------
GRANT CONNECT ON DATABASE clothing_erp TO clothing_app, clothing_bypass, clothing_archiver;

-- ----------------------------------------------------------------------------
-- 表权限（默认）：业务表 CRUD 给 app/bypass，归档专用 archiver 仅 audit_log
-- 实际表权限在 Alembic migration 002 中按表细化
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO clothing_app, clothing_bypass, clothing_archiver;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO clothing_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO clothing_bypass;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO clothing_app, clothing_bypass;
