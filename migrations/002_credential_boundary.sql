-- 002: F-1 credential boundary (red-team finding F-1, docs/RED_TEAM_REPORT.md).
-- The runtime role must not hold table-wide SELECT on users.password_hash.
-- Authentication material is reachable only through the two SECURITY DEFINER
-- functions below: fixed search_path, static SQL, owner = migration role
-- (the table owner), EXECUTE granted exclusively to apex_app.
-- fn_login_material also performs the atomic per-account failed-login budget
-- increment, making the account bucket database-enforced and shared across
-- every application instance and provider by construction.

REVOKE SELECT ON users FROM apex_app;
GRANT SELECT (user_id, name, email, phone, active, created_at) ON users TO apex_app;

CREATE OR REPLACE FUNCTION fn_login_material(p_bucket text, p_email text)
RETURNS TABLE (limited boolean, attempt_count integer, out_user_id text, out_name text,
               out_email text, out_active boolean, out_password_hash text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $fn$
DECLARE v_count integer;
BEGIN
    -- p_bucket is an HMAC hex digest computed by the application (no PII stored).
    IF p_bucket IS NULL OR p_bucket !~ '^[0-9a-f]{64}$'
       OR p_email IS NULL OR length(p_email) > 320 THEN
        limited := false;
        RETURN NEXT;
        RETURN;
    END IF;
    INSERT INTO public.login_attempts(bucket, count, expires_at)
    VALUES (p_bucket, 1, now() + interval '1 minute')
    ON CONFLICT (bucket) DO UPDATE SET
        count = CASE WHEN public.login_attempts.expires_at <= now() THEN 1
                     ELSE public.login_attempts.count + 1 END,
        expires_at = CASE WHEN public.login_attempts.expires_at <= now()
                          THEN now() + interval '1 minute'
                          ELSE public.login_attempts.expires_at END
    RETURNING public.login_attempts.count INTO v_count;
    IF v_count > 5 THEN
        limited := true;
        attempt_count := v_count;
        RETURN NEXT;
        RETURN;
    END IF;
    SELECT u.user_id, u.name, u.email, u.active, u.password_hash
      INTO out_user_id, out_name, out_email, out_active, out_password_hash
      FROM public.users u
      WHERE lower(u.email) = lower(p_email)
      LIMIT 1;
    limited := false;
    attempt_count := v_count;
    RETURN NEXT;
END $fn$;
REVOKE ALL ON FUNCTION fn_login_material(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fn_login_material(text, text) TO apex_app;

-- Successful authentication resets the caller's own account bucket.
CREATE OR REPLACE FUNCTION fn_login_success(p_bucket text)
RETURNS void
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $fn$
    DELETE FROM public.login_attempts
    WHERE bucket = p_bucket AND p_bucket ~ '^[0-9a-f]{64}$';
$fn$;
REVOKE ALL ON FUNCTION fn_login_success(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fn_login_success(text) TO apex_app;
