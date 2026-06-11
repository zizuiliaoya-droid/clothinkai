"""U05 - staging 专用：种子化 e2e-smoke test 测试数据池.

Revision ID: 009_u05_seed_smoke_test_data
Revises: 008_u05_backfill_settlements
Create Date: 2026-05-26

仅 staging 环境运行（通过 ENVIRONMENT=staging 检查）。production 跳过此 migration。

用途（FB1 e2e-smoke 启用 — 详见 deployment-architecture.md §5）：
- deploy-staging.yml::e2e-smoke-after-deploy 需要一个 publish_status='已发布' 的
  dummy promotion 池来跑 J4 review approve → settlement 创建端到端断言
- smoke_test_pr_manager 用户密码由 GitHub Secret 注入（本 migration 仅设占位 hash，
  真实密码通过运维脚本/CI 注入后重置）

幂等：所有 INSERT 用 NOT EXISTS / COUNT 守卫，可重跑。
"""

from __future__ import annotations

import os
from typing import Sequence

from alembic import op

revision: str = "009_u05_seed_smoke_test_data"
down_revision: str | Sequence[str] | None = "008_u05_backfill_settlements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_IS_STAGING = os.getenv("ENVIRONMENT") == "staging"


def upgrade() -> None:
    if not _IS_STAGING:
        return  # production / 本地跳过

    # 1) smoke_test_pr_manager 用户（占位 hash；真实密码由 CI/运维脚本注入后重置）
    #    + 关联 pr_manager 角色
    # 2) 补充 dummy promotion 池到 10 个（publish_status='已发布' + settlement_status='待核查'）
    #    依赖：默认 tenant（003 seed）+ 至少 1 个 style / 1 个 blogger（staging 需预置）
    op.execute(
        r"""
DO $$
DECLARE
    v_tenant_id UUID;
    v_user_id UUID;
    v_role_id UUID;
    v_style_id UUID;
    v_blogger_id UUID;
    v_existing INTEGER;
    i INTEGER;
BEGIN
    -- 取默认租户（003_u01_seed_initial_data 创建）
    SELECT id INTO v_tenant_id FROM tenant ORDER BY created_at ASC LIMIT 1;
    IF v_tenant_id IS NULL THEN
        RAISE NOTICE 'seed skipped: no tenant found';
        RETURN;
    END IF;

    -- 1. smoke_test_pr_manager 用户
    SELECT id INTO v_user_id FROM "user"
    WHERE username = 'smoke_test_pr_manager' AND tenant_id = v_tenant_id;
    IF v_user_id IS NULL THEN
        v_user_id := gen_random_uuid();
        INSERT INTO "user" (
            id, tenant_id, username, password_hash, display_name,
            status, password_must_change, created_at, updated_at
        ) VALUES (
            v_user_id, v_tenant_id, 'smoke_test_pr_manager',
            '!PLACEHOLDER_RESET_VIA_CI!',  -- 无法登录；CI 注入真实 hash 后可用
            'Smoke Test PR Manager', 'active', false, NOW(), NOW()
        );
    END IF;

    -- 关联 pr_manager 角色
    SELECT id INTO v_role_id FROM role WHERE code = 'pr_manager' LIMIT 1;
    IF v_role_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM user_role
        WHERE user_id = v_user_id AND role_id = v_role_id
    ) THEN
        INSERT INTO user_role (tenant_id, user_id, role_id)
        VALUES (v_tenant_id, v_user_id, v_role_id);
    END IF;

    -- 2. dummy promotion 池补足到 10（依赖 staging 已预置 style + blogger）
    SELECT id INTO v_style_id FROM style
    WHERE tenant_id = v_tenant_id AND is_active = true LIMIT 1;
    SELECT id INTO v_blogger_id FROM blogger
    WHERE tenant_id = v_tenant_id AND is_active = true LIMIT 1;

    IF v_style_id IS NULL OR v_blogger_id IS NULL THEN
        RAISE NOTICE 'seed skipped: need at least 1 style + 1 blogger in staging';
        RETURN;
    END IF;

    SELECT COUNT(*) INTO v_existing FROM promotion
    WHERE tenant_id = v_tenant_id AND remark = 'SMOKE_TEST_FIXTURE';

    i := v_existing;
    WHILE i < 10 LOOP
        INSERT INTO promotion (
            id, tenant_id, style_id, blogger_id, pr_id,
            internal_code, style_code_snapshot, style_short_name_snapshot,
            quote_amount, platform, cooperation_date,
            publish_status, recall_status, settlement_status,
            is_active, remark, created_at, updated_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_style_id, v_blogger_id, v_user_id,
            'SMOKE' || LPAD(i::text, 4, '0'), 'SMOKE_STYLE', 'Smoke 测试款',
            500.00, '小红书', CURRENT_DATE,
            '已发布', '未召回', '待核查',
            true, 'SMOKE_TEST_FIXTURE', NOW(), NOW()
        );
        i := i + 1;
    END LOOP;

    RAISE NOTICE 'seed: smoke fixtures ensured (user + % promotions)', 10;
END $$;
"""
    )


def downgrade() -> None:
    if not _IS_STAGING:
        return
    op.execute("DELETE FROM promotion WHERE remark = 'SMOKE_TEST_FIXTURE';")
    op.execute("DELETE FROM \"user\" WHERE username = 'smoke_test_pr_manager';")
