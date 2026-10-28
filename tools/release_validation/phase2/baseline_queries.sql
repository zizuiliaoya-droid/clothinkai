-- Fixed 067 baseline; private complete sets, no business writes.
SELECT p.id,p.tenant_id,b.content_category AS expected_category
FROM promotion p LEFT JOIN blogger b ON b.id=p.blogger_id AND b.tenant_id=p.tenant_id
ORDER BY p.id;
SELECT id,encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') AS stable
FROM blogger_retrospective r ORDER BY id;
SELECT version_num FROM alembic_version;
SELECT rolname,rolsuper,rolbypassrls,rolinherit FROM pg_roles
WHERE rolname IN ('clothing_app','clothing_bypass','clothing_archiver') ORDER BY rolname;
SELECT c.relname,pg_get_userbyid(c.relowner) AS owner,c.relacl,c.relrowsecurity,c.relforcerowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','S') ORDER BY c.relname;
SELECT defaclrole,defaclnamespace,defaclobjtype,defaclacl FROM pg_default_acl ORDER BY 1,2,3;
SELECT roleid,member,admin_option FROM pg_auth_members ORDER BY 1,2;
