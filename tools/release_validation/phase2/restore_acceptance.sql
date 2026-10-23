-- Candidate SELECT only, against the isolated Unix-socket database; never production restore.
-- Compare protected canonical output with the same queries captured before backup.
SELECT current_database(), current_user, current_setting('server_version_num');
SELECT version_num FROM alembic_version;
SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner),c.relacl,c.relrowsecurity,c.relforcerowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','S') ORDER BY c.relname;
SELECT conrelid::regclass::text,conname,contype,pg_get_constraintdef(oid)
FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1,2;
SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2;
SELECT defaclrole,defaclnamespace,defaclobjtype,defaclacl FROM pg_default_acl ORDER BY 1,2,3;
SELECT rolname,rolsuper,rolbypassrls,rolinherit FROM pg_roles
WHERE rolname IN ('clothing_app','clothing_bypass','clothing_archiver') ORDER BY rolname;
-- Ordinary-role RLS checks require separate controlled sessions and are not proven by this superuser view.
