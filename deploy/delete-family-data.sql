-- P10-07: privately verify FULL family deletion; stop ALL app writers first.
-- Use psql -X -v family_id=<confirmed UUID> -f deploy/delete-family-data.sql.
-- This is NOT an online operation; restart only after commit to clear drafts.
\set ON_ERROR_STOP on
\set QUIET on
\set VERBOSITY terse
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE families, wards, relatives, invitations, check_ins, link_codes,
           link_attempts, silence_alerts, outbox IN SHARE ROW EXCLUSIVE MODE;
SELECT :'family_id' ~* '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$' AS valid \gset
\if :valid
\else
  \echo 'Invalid target UUID; rolled back.'
  DO $$ BEGIN RAISE EXCEPTION 'Target validation failed'; END $$;
\endif
CREATE TEMP TABLE delete_target ON COMMIT DROP AS
SELECT id FROM families WHERE id=:'family_id'::uuid;
SELECT EXISTS(SELECT 1 FROM delete_target) AS found \gset
\if :found
\else
  \echo 'Target not found; rolled back.'
  DO $$ BEGIN RAISE EXCEPTION 'Target validation failed'; END $$;
\endif
WITH removed AS (
  DELETE FROM outbox o WHERE
    EXISTS (SELECT 1 FROM relatives r JOIN delete_target t ON r.family_id=t.id
            WHERE o.chat_id=r.chat_id)
    OR EXISTS (SELECT 1 FROM check_ins c JOIN wards w ON c.ward_id=w.id
               JOIN delete_target t ON w.family_id=t.id WHERE o.event_key='checkin:' || c.id::text)
    OR EXISTS (SELECT 1 FROM wards w JOIN delete_target t ON w.family_id=t.id
               WHERE o.event_key LIKE 'silence:' || w.id::text || ':%') RETURNING 1
) SELECT count(*) AS deliveries_removed FROM removed;
WITH removed AS (
  DELETE FROM link_attempts a USING wards w, delete_target t
  WHERE w.family_id=t.id AND a.application_id=w.device_id RETURNING 1
) SELECT count(*) AS attempts_removed FROM removed;
WITH removed AS (
  DELETE FROM families f USING delete_target t WHERE f.id=t.id RETURNING 1
) SELECT count(*) AS families_removed FROM removed;
COMMIT;
