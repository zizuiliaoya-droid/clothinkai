-- Fixed 068/069 changes only. Existing business data is compared to the 067 baseline.
SELECT version_num FROM alembic_version;
SELECT id,content_category AS expected_category FROM promotion ORDER BY id;
SELECT column_name,data_type,character_maximum_length,is_nullable,column_default
FROM information_schema.columns WHERE table_schema='public' AND table_name='promotion'
AND column_name='content_category' ORDER BY ordinal_position;
SELECT column_name,data_type,character_maximum_length,is_nullable,column_default
FROM information_schema.columns WHERE table_schema='public' AND table_name='promotion_platform_post'
ORDER BY ordinal_position;
SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint
WHERE conrelid=to_regclass('public.promotion_platform_post') ORDER BY definition;
SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='promotion_platform_post' ORDER BY indexdef;
SELECT pg_get_userbyid(relowner) AS owner,relrowsecurity,relforcerowsecurity,
has_table_privilege('clothing_app',oid,'SELECT') AS app_select,
has_table_privilege('clothing_app',oid,'INSERT') AS app_insert,
has_table_privilege('clothing_app',oid,'UPDATE') AS app_update,
has_table_privilege('clothing_app',oid,'DELETE') AS app_delete,
has_table_privilege('clothing_app',oid,'TRUNCATE') AS app_truncate,
has_table_privilege('clothing_app',oid,'REFERENCES') AS app_references,
has_table_privilege('clothing_app',oid,'TRIGGER') AS app_trigger
FROM pg_class WHERE oid=to_regclass('public.promotion_platform_post');
SELECT policyname,permissive,roles,cmd,qual,with_check FROM pg_policies
WHERE schemaname='public' AND tablename='promotion_platform_post' ORDER BY policyname;
SELECT count(*) AS platform_post_count FROM promotion_platform_post;
