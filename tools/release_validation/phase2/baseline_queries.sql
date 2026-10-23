-- Candidate SELECT only. Run through a reviewed read-only collector with per-statement limits.
-- Raw IDs/status/ACL are protected server evidence, never model/public report output.
-- PRE: capture exact stable ID sets before 067. No completed_at column exists yet.
SELECT id, tenant_id, publish_status, settlement_status, recall_status, retro_status,
       metrics_recorded_at
FROM promotion
WHERE retro_status='待确认' OR (publish_status='已发布' AND retro_status='未开始')
   OR (publish_status='已发布' AND settlement_status='已结款'
       AND metrics_recorded_at IS NOT NULL AND retro_status='已完成')
   OR (publish_status='已取消' AND recall_status='召回成功')
ORDER BY tenant_id,id;
SELECT id,tenant_id,confirmed_at FROM blogger_retrospective WHERE confirmed_at IS NULL ORDER BY tenant_id,id;
SELECT version_num FROM alembic_version;
SELECT rolname,rolsuper,rolbypassrls,rolinherit FROM pg_roles
WHERE rolname IN ('clothing_app','clothing_bypass','clothing_archiver') ORDER BY rolname;
SELECT c.relname,pg_get_userbyid(c.relowner) AS owner,c.relacl,c.relrowsecurity,c.relforcerowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','S') ORDER BY c.relname;
SELECT defaclrole,defaclnamespace,defaclobjtype,defaclacl FROM pg_default_acl ORDER BY 1,2,3;
SELECT roleid,member,admin_option FROM pg_auth_members ORDER BY 1,2;
