-- Audit tables (Ch 8 and Ch 9).
--
-- audit.authz: every entitlement and policy decision taken on a decision's behalf.
-- audit.packet: one sealed audit packet per filed decision, hash-chained. Each row
-- carries the SHA-256 of the previous row's hash plus its own canonical packet, so
-- editing any packet, or deleting or reordering rows, breaks every hash after it.

DROP SCHEMA IF EXISTS audit CASCADE;
CREATE SCHEMA audit;

CREATE TABLE audit.authz (
    id           bigserial PRIMARY KEY,
    decision_id  text,                 -- the decision being assessed, when known
    at           timestamptz NOT NULL DEFAULT now(),
    engine       text NOT NULL CHECK (engine IN ('openfga', 'opa', 'rls')),
    subject      text NOT NULL,        -- user:<username>
    action       text NOT NULL,        -- relation or tool name
    object       text NOT NULL,        -- application:APP-H1, tool:get_figures
    allowed      boolean NOT NULL,
    detail       jsonb
);
CREATE INDEX ON audit.authz (decision_id);

CREATE TABLE audit.packet (
    seq            bigserial PRIMARY KEY,
    decision_id    uuid NOT NULL UNIQUE,
    packet         jsonb NOT NULL,
    packet_sha256  text NOT NULL,      -- sha256 of the canonical packet JSON
    prev_hash      text NOT NULL,      -- the previous row's chain_hash ('genesis' for the first)
    chain_hash     text NOT NULL,      -- sha256(prev_hash || packet_sha256)
    sealed_at      timestamptz NOT NULL DEFAULT now()
);

-- Append-only for everyone but the owner: the agent's role may add packets, never change them.
DO $$ BEGIN
    CREATE ROLE brightwater_app LOGIN PASSWORD 'brightwater_app' NOBYPASSRLS;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT USAGE ON SCHEMA audit TO brightwater_app;
GRANT SELECT, INSERT ON audit.packet, audit.authz TO brightwater_app;
GRANT USAGE ON SEQUENCE audit.packet_seq_seq, audit.authz_id_seq TO brightwater_app;

