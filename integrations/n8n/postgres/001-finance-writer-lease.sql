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

REVOKE ALL ON SCHEMA finance_ops FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA finance_ops FROM PUBLIC;

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
    ON CONFLICT (resource_key) DO UPDATE
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

CREATE OR REPLACE FUNCTION finance_ops.release_writer_lease(
    p_resource_key text,
    p_lease_id uuid,
    p_fencing_token bigint
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, finance_ops AS $$
DECLARE changed integer;
BEGIN
    UPDATE finance_ops.writer_leases
       SET released_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE resource_key = p_resource_key
       AND lease_id = p_lease_id
       AND fencing_token = p_fencing_token
       AND released_at IS NULL;
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed = 1;
END;
$$;

COMMIT;
