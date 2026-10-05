-- P10-07: verify the request privately; stop ALL app writers before running.
-- Use psql -X -v ward_id=<confirmed UUID> -f deploy/revoke-consent.sql.
-- Restart only after commit: this discards stale in-memory bot drafts.
\set ON_ERROR_STOP on
\set QUIET on
\set VERBOSITY terse
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE wards, check_ins, link_codes, link_attempts, outbox IN SHARE ROW EXCLUSIVE MODE;
SELECT :'ward_id' ~* '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$' AS valid \gset
\if :valid
\else
  \echo 'Invalid target UUID; rolled back.'
  DO $$ BEGIN RAISE EXCEPTION 'Target validation failed'; END $$;
\endif
CREATE TEMP TABLE revoke_target ON COMMIT DROP AS
SELECT id, device_id FROM wards WHERE id = :'ward_id'::uuid;
SELECT EXISTS(SELECT 1 FROM revoke_target) AS found \gset
\if :found
\else
  \echo 'Target not found; rolled back.'
  DO $$ BEGIN RAISE EXCEPTION 'Target validation failed'; END $$;
\endif
WITH removed AS (
  DELETE FROM outbox o WHERE delivered_at IS NULL AND (
    EXISTS (SELECT 1 FROM check_ins c JOIN revoke_target t ON c.ward_id=t.id
            WHERE o.event_key='checkin:' || c.id::text)
    OR EXISTS (SELECT 1 FROM revoke_target t
               WHERE o.event_key LIKE 'silence:' || t.id::text || ':%')) RETURNING 1
) SELECT count(*) AS pending_deliveries_removed FROM removed;
WITH removed AS (
  DELETE FROM link_codes c USING revoke_target t WHERE c.ward_id=t.id RETURNING 1
) SELECT count(*) AS codes_removed FROM removed;
WITH removed AS (
  DELETE FROM link_attempts a USING revoke_target t
  WHERE a.application_id=t.device_id RETURNING 1
) SELECT count(*) AS attempts_removed FROM removed;
WITH removed AS (
  DELETE FROM check_ins c USING revoke_target t
  WHERE c.ward_id=t.id AND c.completed_at IS NULL RETURNING 1
) SELECT count(*) AS incomplete_checkins_removed FROM removed;
WITH changed AS (
  UPDATE wards w SET device_id=NULL, consent_at=NULL, consent_version=NULL
  FROM revoke_target t WHERE w.id=t.id RETURNING 1
) SELECT count(*) AS wards_revoked FROM changed;
COMMIT;
