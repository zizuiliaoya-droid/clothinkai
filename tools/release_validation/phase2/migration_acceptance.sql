-- Candidate SELECT projections only; never executed. Retain full baseline IDs privately.
-- Compare exact row identity sets in migration_adapter; an equal count is insufficient.
SELECT version_num FROM alembic_version;
SELECT id, completed_at FROM promotion WHERE completed_at IS NOT NULL ORDER BY id;
SELECT id, retro_status FROM promotion WHERE id=ANY(CAST(:pre_mapping_ids AS uuid[])) ORDER BY id;
SELECT id, confirmed_at FROM blogger_retrospective WHERE id=ANY(CAST(:pre_unconfirmed_ids AS uuid[])) ORDER BY id;
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
 WHERE conrelid='promotion'::regclass AND conname='ck_promotion_ck_promotion_retro_status';
SELECT indexname,indexdef FROM pg_indexes WHERE tablename='promotion' AND indexname='idx_promotion_completed';
SELECT data_type FROM information_schema.columns WHERE table_schema='public'
 AND table_name='promotion' AND column_name='completed_at';
SELECT p.scope,r.code FROM permission p LEFT JOIN role_permission rp ON rp.permission_id=p.id
 LEFT JOIN role r ON r.id=rp.role_id WHERE p.scope='promotion_retro:write' ORDER BY r.code;
-- Migration backfill does not create business completion events. Separate H1/H2 from H3.
SELECT id,event_type,created_at FROM line_event WHERE created_at>=CAST(:migration_started_at AS timestamptz)
 AND created_at<=CAST(:migration_observed_at AS timestamptz) AND event_type='promotion.completed' ORDER BY id;
-- After actual open, observe real recall-completion events only; do not infer from updated_at.
SELECT id,promotion_id,created_at FROM line_event WHERE event_type='promotion.completed'
 AND payload->>'completion_kind'='召回' AND created_at>=CAST(:opened_at AS timestamptz) ORDER BY id;
