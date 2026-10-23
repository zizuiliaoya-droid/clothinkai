-- SELECT-only candidate. Normalize/compare role and K7/K8 projections privately.
-- No actual role session, tenant binding or K8 business collector is implemented here.
SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname='clothing_app';
SELECT version_num FROM alembic_version;
SELECT count(*) AS old_status_count FROM promotion WHERE retro_status='待确认';
SELECT count(*) AS published_unstarted_count FROM promotion WHERE publish_status='已发布' AND retro_status='未开始';
SELECT count(*) AS new_unconfirmed FROM blogger_retrospective
 WHERE created_at>=CAST(:opened_at AS timestamptz) AND confirmed_at IS NULL;
-- Absence of new samples remains pending; never insert synthetic customer records for acceptance.
