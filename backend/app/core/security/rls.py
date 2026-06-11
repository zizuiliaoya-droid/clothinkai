"""PostgreSQL Row Level Security 策略 SQL 模板。

供 Alembic migration 使用。运行时不参与查询，仅 migration 阶段生成 DDL。

策略契约（详见 nfr-design-patterns.md 第 2.4 节）：
    USING (
        tenant_id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.bypass_rls', true) = 'on'
    )
"""

from __future__ import annotations


# RLS 策略谓词（USING / WITH CHECK 共用）
_POLICY_PREDICATE = (
    "tenant_id = current_setting('app.tenant_id', true)::uuid "
    "OR current_setting('app.bypass_rls', true) = 'on'"
)


def _dollar_quote(sql_statements: list[str], *, tag: str = "rls") -> str:
    """把多条 DDL 包进单个 PL/pgSQL DO 块（asyncpg 单语句执行）。

    背景：asyncpg 的 prepared-statement 协议不允许在一次 ``execute`` 中发送
    多条命令（"cannot insert multiple commands into a prepared statement"）。
    Alembic env.py 用 asyncpg 驱动，故 ``op.execute`` 接收的 SQL 必须是单语句。
    用 ``DO $tag$ ... $tag$`` 包裹 + ``EXECUTE`` 动态执行，整体是单条语句。

    每条内部语句通过 ``EXECUTE '...'`` 运行，其中的单引号按 PL/pgSQL 规则
    翻倍转义（``'`` → ``''``）。
    """
    lines = [f"DO ${tag}$", "BEGIN"]
    for stmt in sql_statements:
        escaped = stmt.replace("'", "''")
        lines.append(f"    EXECUTE '{escaped}';")
    lines.append("END")
    lines.append(f"${tag}$;")
    return "\n".join(lines)


def enable_rls_sql(table_name: str) -> str:
    """生成启用 RLS 的 DDL（FOR ALL TO clothing_app）。

    返回单条 ``DO`` 块语句（asyncpg 兼容），内部执行 3 条 DDL：
    ENABLE RLS / FORCE RLS / CREATE POLICY tenant_isolation。
    """
    statements = [
        f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY',
        f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY',
        (
            f'CREATE POLICY tenant_isolation ON "{table_name}" '
            f"FOR ALL TO clothing_app "
            f"USING ({_POLICY_PREDICATE}) "
            f"WITH CHECK ({_POLICY_PREDICATE})"
        ),
    ]
    return _dollar_quote(statements)


def disable_rls_sql(table_name: str) -> str:
    """回滚 RLS（downgrade 用）。单条 ``DO`` 块（asyncpg 兼容）。"""
    statements = [
        f'DROP POLICY IF EXISTS tenant_isolation ON "{table_name}"',
        f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY',
    ]
    return _dollar_quote(statements)


def revoke_audit_log_modifications_sql() -> str:
    """audit_log 仅允许 INSERT + SELECT，禁止 UPDATE/DELETE 给 clothing_app。

    归档专用角色 clothing_archiver 持有 DELETE 权限。
    """
    return """
REVOKE UPDATE, DELETE ON audit_log FROM clothing_app;
GRANT SELECT, DELETE ON audit_log TO clothing_archiver;
""".strip()
