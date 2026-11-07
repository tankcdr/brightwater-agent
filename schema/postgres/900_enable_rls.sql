-- Chapter 8: switch on row-level security for the agent's own database role.
--
-- The policies were defined in 002_rls.sql (Ch 0). Here we create the role the
-- agent's tools connect as, grant it what it needs, and enable RLS on every
-- table that has a policy. The owner role (brightwater) still bypasses RLS: Cube
-- reads across coverage on purpose, and entitlement to an application is checked
-- in OpenFGA before any figure is read.

DO $$ BEGIN
    CREATE ROLE brightwater_app LOGIN PASSWORD 'brightwater_app' NOBYPASSRLS;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

GRANT USAGE ON SCHEMA ledger, credit, compliance, registry, relationship, agent TO brightwater_app;
GRANT SELECT ON ALL TABLES IN SCHEMA ledger, credit, compliance, registry, relationship TO brightwater_app;
GRANT SELECT, INSERT, UPDATE ON agent.decision TO brightwater_app;
GRANT EXECUTE ON FUNCTION relationship.current_banker(), relationship.covers(text) TO brightwater_app;

ALTER TABLE ledger.business          ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.deposit_account   ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.monthly_cash_flow ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit.facility          ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit.payment           ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit.application       ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance.kyc           ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance.adverse_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE relationship.note        ENABLE ROW LEVEL SECURITY;
