-- Before opening: no customer records are inserted for acceptance.
SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname='clothing_app';
SELECT version_num FROM alembic_version;
SELECT count(*) AS content_category_mismatch_count FROM promotion p
LEFT JOIN blogger b ON b.id=p.blogger_id AND b.tenant_id=p.tenant_id
WHERE p.content_category IS DISTINCT FROM b.content_category;
SELECT count(*) AS platform_post_count FROM promotion_platform_post;
