-- Barrier 1 of 4 (Plan.md section 8.2): the database itself refuses to write.
--
-- This file creates the login role the AI agent connects as. It is the only
-- principal the agent ever uses, and it is incapable of modifying anything --
-- not because the application declines to, but because PostgreSQL will not let
-- it. Every later phase inherits that guarantee instead of re-asserting it.
--
-- NO TABLE IS CREATED OR ALTERED HERE. Only a role and its grants.
--
-- Idempotent: safe to run against a live database and safe to re-run. It is
-- included from database/init/00_bootstrap.sql so a fresh volume gets it
-- automatically, and it can be applied to an already-initialised database with
--     psql -U hr_user -d mock_hr -f /database/schema/hr/10_readonly_role.sql

\set ON_ERROR_STOP on

-- --------------------------------------------------------------------------
-- The role
-- --------------------------------------------------------------------------
-- NOINHERIT so the role can never pick up privileges by being granted
-- membership in some future group; NOCREATEDB / NOCREATEROLE / NOSUPERUSER are
-- the defaults but are stated because this is a security boundary and defaults
-- are easy to change by accident.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mock_hr_readonly') THEN
        CREATE ROLE mock_hr_readonly
            LOGIN
            NOINHERIT
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOREPLICATION
            NOBYPASSRLS
            PASSWORD 'readonly_password';
    END IF;
END
$$;

-- Re-asserted on every run: a role altered by hand is put back where it belongs.
ALTER ROLE mock_hr_readonly
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- --------------------------------------------------------------------------
-- Grants: exactly SELECT, and nothing else
-- --------------------------------------------------------------------------
GRANT CONNECT ON DATABASE mock_hr TO mock_hr_readonly;
GRANT USAGE  ON SCHEMA public     TO mock_hr_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mock_hr_readonly;

-- Tables added later are readable too, without anyone remembering to re-grant.
-- Scoped to the role that owns the HR objects, which is the only role that
-- creates them.
ALTER DEFAULT PRIVILEGES FOR ROLE hr_user IN SCHEMA public
    GRANT SELECT ON TABLES TO mock_hr_readonly;

-- Belt and braces on top of PostgreSQL 15+ defaults: PUBLIC must not be able to
-- create objects in `public`, or `CREATE TABLE` would succeed for any role.
-- The schema owner (and therefore hr_user, the database owner) is unaffected.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- No write privilege is ever granted. Stated explicitly so that a reader looking
-- for the missing GRANT finds this comment instead of assuming an omission.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA public FROM mock_hr_readonly;

-- Sequences are deliberately not granted: USAGE on a sequence permits nextval(),
-- which mutates sequence state and is therefore a write.
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM mock_hr_readonly;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM mock_hr_readonly;

-- --------------------------------------------------------------------------
-- Session defaults applied at login
-- --------------------------------------------------------------------------
-- default_transaction_read_only makes every transaction READ ONLY even if the
-- application forgets to say so. The application says so anyway (barrier 2) --
-- that redundancy is the point.
ALTER ROLE mock_hr_readonly SET default_transaction_read_only = on;

-- A runaway query must not hold a connection open forever. 15s matches
-- TOOL_TIMEOUT_SECONDS in Plan.md section 5.3; the application narrows it
-- further per unit of work.
ALTER ROLE mock_hr_readonly SET statement_timeout = '15s';
ALTER ROLE mock_hr_readonly SET idle_in_transaction_session_timeout = '30s';

-- Keeps a lock-wait from becoming an indefinite hang.
ALTER ROLE mock_hr_readonly SET lock_timeout = '5s';

\echo '=== mock_hr :: role mock_hr_readonly ready (SELECT only, read-only txns) ==='
