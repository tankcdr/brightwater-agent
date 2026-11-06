-- Row-level security: defined now, switched on in Chapter 8.
--
-- The rule a bank actually uses: a banker sees a business only if they cover
-- it. The banker's identity arrives as a session setting
-- (`brightwater.banker_id`), which Ch 8 sets from the Keycloak token on every
-- connection the agent opens on a banker's behalf.
--
-- Postgres lets you create policies on a table without enabling RLS; they
-- simply do nothing until `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`. That
-- is the state this file leaves the database in. `schema/postgres/
-- 900_enable_rls.sql` (Ch 8) flips the switch.

CREATE OR REPLACE FUNCTION relationship.current_banker() RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('brightwater.banker_id', true), '')
$$;

CREATE OR REPLACE FUNCTION relationship.covers(p_business_id text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = relationship AS $$
    SELECT EXISTS (
        SELECT 1 FROM relationship.coverage c
        WHERE c.business_id = p_business_id
          AND c.banker_id = relationship.current_banker()
    )
$$;

CREATE POLICY banker_coverage ON ledger.business
    USING (relationship.covers(business_id));
CREATE POLICY banker_coverage ON ledger.deposit_account
    USING (relationship.covers(business_id));
CREATE POLICY banker_coverage ON ledger.monthly_cash_flow
    USING (EXISTS (SELECT 1 FROM ledger.deposit_account a
                   WHERE a.account_id = monthly_cash_flow.account_id
                     AND relationship.covers(a.business_id)));
CREATE POLICY banker_coverage ON credit.facility
    USING (relationship.covers(business_id));
CREATE POLICY banker_coverage ON credit.payment
    USING (EXISTS (SELECT 1 FROM credit.facility f
                   WHERE f.facility_id = payment.facility_id
                     AND relationship.covers(f.business_id)));
CREATE POLICY banker_coverage ON credit.application
    USING (relationship.covers(business_id));
CREATE POLICY banker_coverage ON compliance.kyc
    USING (relationship.covers(business_id));
CREATE POLICY banker_coverage ON compliance.adverse_event
    USING (relationship.covers(business_id));
CREATE POLICY banker_coverage ON relationship.note
    USING (relationship.covers(business_id));

-- Ownership is deliberately NOT restricted to covered businesses. A banker
-- assessing an applicant must see the owners' stakes in businesses someone
-- else covers; that is the whole point of the group exposure and related
-- delinquency rules. What they may not see is the other business's ledger.
-- Ch 8 revisits this with OpenFGA, where "may see the stake" and "may see
-- the account" become separate relations.
