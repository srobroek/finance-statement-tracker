BEGIN;

CREATE SCHEMA IF NOT EXISTS finance_ops;

CREATE TABLE IF NOT EXISTS finance_ops.writer_leases (
    resource_key text PRIMARY KEY,
    lease_id uuid NOT NULL,
    lease_owner text NOT NULL,
    fencing_token bigint NOT NULL CHECK (fencing_token > 0),
    expires_at timestamptz NOT NULL,
    released_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS finance_ops.actual_writer_effects (
    resource_key text NOT NULL,
    outbox_id text NOT NULL,
    account_id text NOT NULL,
    budget_id text NOT NULL,
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    verified_payload_sha256 text CHECK (verified_payload_sha256 IS NULL OR verified_payload_sha256 ~ '^[0-9a-f]{64}$'),
    period_start date NOT NULL,
    period_end date NOT NULL CHECK (period_start <= period_end),
    state text NOT NULL CHECK (state IN ('PREPARED', 'ISSUED', 'ACTUAL_OBSERVED', 'OUTCOME_UNKNOWN', 'VERIFIED', 'RECONCILED', 'COMMITTED')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_id uuid,
    lease_owner text,
    fencing_token bigint CHECK (fencing_token IS NULL OR fencing_token > 0),
    issued_at timestamptz,
    last_error_class text,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((lease_id IS NULL AND lease_owner IS NULL AND fencing_token IS NULL) OR (lease_id IS NOT NULL AND lease_owner IS NOT NULL AND fencing_token IS NOT NULL)),
    CHECK (state NOT IN ('ISSUED', 'ACTUAL_OBSERVED', 'OUTCOME_UNKNOWN') OR (lease_id IS NOT NULL AND lease_owner IS NOT NULL AND fencing_token IS NOT NULL)),
    CHECK (state NOT IN ('VERIFIED', 'RECONCILED', 'COMMITTED') OR verified_payload_sha256 IS NOT NULL),
    PRIMARY KEY (resource_key, outbox_id)
);

CREATE INDEX IF NOT EXISTS actual_writer_effects_admission_idx
    ON finance_ops.actual_writer_effects (resource_key, state, updated_at);

CREATE TABLE IF NOT EXISTS finance_ops.actual_writer_releases (
    resource_key text NOT NULL,
    outbox_id text NOT NULL,
    account_id text NOT NULL,
    budget_id text NOT NULL,
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    verified_payload_sha256 text NOT NULL CHECK (verified_payload_sha256 ~ '^[0-9a-f]{64}$'),
    period_start date NOT NULL,
    period_end date NOT NULL CHECK (period_start <= period_end),
    state text NOT NULL CHECK (state IN ('VERIFIED', 'RECONCILED', 'COMMITTED')),
    lease_id uuid NOT NULL,
    lease_owner text NOT NULL,
    fencing_token bigint NOT NULL CHECK (fencing_token > 0),
    released_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (resource_key, outbox_id, fencing_token),
    UNIQUE (resource_key, lease_id, fencing_token)
);

CREATE INDEX IF NOT EXISTS actual_writer_releases_lookup_idx
    ON finance_ops.actual_writer_releases (resource_key, outbox_id, fencing_token);



REVOKE ALL ON SCHEMA finance_ops FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA finance_ops FROM PUBLIC;
GRANT USAGE ON SCHEMA finance_ops TO n8n;
GRANT SELECT, INSERT, UPDATE ON finance_ops.actual_writer_effects TO n8n;
GRANT SELECT ON finance_ops.actual_writer_releases TO n8n;

DROP FUNCTION IF EXISTS finance_ops.acquire_writer_lease(text, uuid, text, integer);

CREATE OR REPLACE FUNCTION finance_ops.acquire_writer_lease(
    p_resource_key text,
    p_lease_owner text,
    p_ttl_seconds integer
) RETURNS TABLE (
    resource_key text,
    lease_id uuid,
    lease_owner text,
    fencing_token bigint,
    expires_at timestamptz
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, finance_ops AS $$
BEGIN
    IF p_resource_key !~ '^actual:[A-Za-z0-9_-]{1,128}$' THEN
        RAISE EXCEPTION 'invalid writer resource key';
    END IF;
    IF p_lease_owner !~ '^n8n:[A-Za-z0-9:_-]{1,160}$' THEN
        RAISE EXCEPTION 'invalid writer lease owner';
    END IF;
    IF p_ttl_seconds < 30 OR p_ttl_seconds > 600 THEN
        RAISE EXCEPTION 'invalid writer lease ttl';
    END IF;

    RETURN QUERY
    INSERT INTO finance_ops.writer_leases AS current (
        resource_key, lease_id, lease_owner, fencing_token, expires_at,
        released_at, updated_at
    ) VALUES (
        p_resource_key, gen_random_uuid(), p_lease_owner, 1,
        clock_timestamp() + make_interval(secs => p_ttl_seconds),
        NULL, clock_timestamp()
    )
    ON CONFLICT ON CONSTRAINT writer_leases_pkey DO UPDATE
    SET lease_id = EXCLUDED.lease_id,
        lease_owner = EXCLUDED.lease_owner,
        fencing_token = current.fencing_token + 1,
        expires_at = EXCLUDED.expires_at,
        released_at = NULL,
        updated_at = clock_timestamp()
    WHERE current.released_at IS NOT NULL OR current.expires_at <= clock_timestamp()
    RETURNING current.resource_key, current.lease_id, current.lease_owner,
              current.fencing_token, current.expires_at;
END;
$$;

REVOKE ALL ON FUNCTION finance_ops.acquire_writer_lease(text, text, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION finance_ops.acquire_writer_lease(text, text, integer) TO n8n;


CREATE OR REPLACE FUNCTION finance_ops.assert_writer_lease(
    p_resource_key text,
    p_lease_id uuid,
    p_fencing_token bigint
) RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, finance_ops AS $$
    SELECT EXISTS (
        SELECT 1 FROM finance_ops.writer_leases
        WHERE resource_key = p_resource_key
          AND lease_id = p_lease_id
          AND fencing_token = p_fencing_token
          AND released_at IS NULL
          AND expires_at > clock_timestamp()
    );
$$;

REVOKE ALL ON FUNCTION finance_ops.assert_writer_lease(text, uuid, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION finance_ops.assert_writer_lease(text, uuid, bigint) TO n8n;


CREATE OR REPLACE FUNCTION finance_ops.release_writer_lease(
    p_resource_key text,
    p_lease_id uuid,
    p_fencing_token bigint
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, finance_ops AS $$
DECLARE
    changed integer;
BEGIN
    -- Retry immutable release evidence before consulting the mutable effect
    -- row, whose lease fields may already belong to a later successor.
    IF EXISTS (
        SELECT 1
          FROM finance_ops.actual_writer_releases
         WHERE resource_key = p_resource_key
           AND lease_id = p_lease_id
           AND fencing_token = p_fencing_token
    ) THEN
        RETURN true;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM finance_ops.actual_writer_effects
         WHERE resource_key = p_resource_key
           AND lease_id = p_lease_id
           AND fencing_token = p_fencing_token
           AND state = 'COMMITTED'
           AND verified_payload_sha256 IS NOT NULL
    ) THEN
        RETURN false;
    END IF;

    UPDATE finance_ops.writer_leases
       SET released_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE resource_key = p_resource_key
       AND lease_id = p_lease_id
       AND fencing_token = p_fencing_token
       AND released_at IS NULL;
    GET DIAGNOSTICS changed = ROW_COUNT;

    IF changed = 1 OR EXISTS (
        SELECT 1
          FROM finance_ops.writer_leases
         WHERE resource_key = p_resource_key
           AND lease_id = p_lease_id
           AND fencing_token = p_fencing_token
           AND released_at IS NOT NULL
    ) THEN
        INSERT INTO finance_ops.actual_writer_releases (
            resource_key, outbox_id, account_id, budget_id, payload_sha256,
            verified_payload_sha256, period_start, period_end, state,
            lease_id, lease_owner, fencing_token, released_at
        )
        SELECT resource_key, outbox_id, account_id, budget_id, payload_sha256,
               verified_payload_sha256, period_start, period_end, state,
               lease_id, lease_owner, fencing_token, clock_timestamp()
          FROM finance_ops.actual_writer_effects
         WHERE resource_key = p_resource_key
           AND lease_id = p_lease_id
           AND fencing_token = p_fencing_token
           AND state = 'COMMITTED'
           AND verified_payload_sha256 IS NOT NULL
        ON CONFLICT (resource_key, outbox_id, fencing_token) DO NOTHING;
    END IF;

    -- Release is retry-safe for the exact historical lease. The immutable
    -- release row remains valid after a later lease acquisition overwrites
    -- the single current writer_leases row.
    RETURN EXISTS (
        SELECT 1
          FROM finance_ops.actual_writer_releases
         WHERE resource_key = p_resource_key
           AND lease_id = p_lease_id
           AND fencing_token = p_fencing_token
    );
END;
$$;

REVOKE ALL ON FUNCTION finance_ops.release_writer_lease(text, uuid, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION finance_ops.release_writer_lease(text, uuid, bigint) TO n8n;

COMMIT;
