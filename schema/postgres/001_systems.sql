-- Brightwater Community Bank: the five systems of record.
--
-- Each system is its own schema, the way they would be separate applications
-- in a real bank. Nothing here joins across schemas with a foreign key: in
-- production these would live in different databases owned by different
-- teams, and the business_id is the only shared key. Keeping that discipline
-- now is what makes the cross-system facts in later chapters honest.

DROP SCHEMA IF EXISTS ledger, credit, compliance, registry, relationship CASCADE;

CREATE SCHEMA ledger;        -- businesses, deposit accounts, monthly flows
CREATE SCHEMA credit;        -- facilities, payment history, applications
CREATE SCHEMA compliance;    -- KYC, adverse events, the policy as rule rows
CREATE SCHEMA registry;      -- people and ownership stakes
CREATE SCHEMA relationship;  -- bankers, coverage, free-text notes

---------------------------------------------------------------- ledger
CREATE TABLE ledger.business (
    business_id     text PRIMARY KEY,
    legal_name      text NOT NULL,
    naics           text NOT NULL,
    state           char(2) NOT NULL,
    established     date NOT NULL,
    customer_since  date NOT NULL
);

CREATE TABLE ledger.deposit_account (
    account_id   text PRIMARY KEY,
    business_id  text NOT NULL REFERENCES ledger.business,
    opened       date NOT NULL
);

-- One row per account per calendar month. Inflows are split so that a
-- definition of "cash available for debt service" can choose what to count.
CREATE TABLE ledger.monthly_cash_flow (
    account_id           text NOT NULL REFERENCES ledger.deposit_account,
    month                date NOT NULL CHECK (extract(day FROM month) = 1),
    operating_inflow     numeric(14,2) NOT NULL CHECK (operating_inflow >= 0),
    nonoperating_inflow  numeric(14,2) NOT NULL DEFAULT 0 CHECK (nonoperating_inflow >= 0),
    outflow              numeric(14,2) NOT NULL CHECK (outflow >= 0),
    PRIMARY KEY (account_id, month)
);

---------------------------------------------------------------- credit
CREATE TABLE credit.facility (
    facility_id          text PRIMARY KEY,
    business_id          text NOT NULL,
    kind                 text NOT NULL CHECK (kind IN ('term', 'line')),
    opened               date NOT NULL,
    original_amount      numeric(14,2) NOT NULL,
    outstanding_balance  numeric(14,2) NOT NULL,
    monthly_payment      numeric(14,2) NOT NULL
);

CREATE TABLE credit.payment (
    facility_id  text NOT NULL REFERENCES credit.facility,
    due_date     date NOT NULL,
    amount_due   numeric(14,2) NOT NULL,
    paid_date    date,
    amount_paid  numeric(14,2),
    PRIMARY KEY (facility_id, due_date)
);

CREATE TABLE credit.application (
    application_id    text PRIMARY KEY,
    business_id       text NOT NULL,
    requested_amount  numeric(14,2) NOT NULL CHECK (requested_amount > 0),
    term_months       integer NOT NULL CHECK (term_months > 0),
    annual_rate       numeric(6,4) NOT NULL CHECK (annual_rate > 0),
    submitted_on      date NOT NULL,
    purpose           text NOT NULL,
    status            text NOT NULL DEFAULT 'open'
);

---------------------------------------------------------------- compliance
CREATE TABLE compliance.kyc (
    business_id      text PRIMARY KEY,
    status           text NOT NULL CHECK (status IN ('current', 'lapsed', 'pending')),
    last_reviewed    date NOT NULL,
    next_review_due  date NOT NULL
);

CREATE TABLE compliance.adverse_event (
    event_id     text PRIMARY KEY,
    business_id  text NOT NULL,
    occurred_on  date NOT NULL,
    kind         text NOT NULL,
    summary      text NOT NULL
);

-- The lending policy as rows. src/brightwater/policy.py is the source; the
-- `brightwater policy sync` command writes it here so that the policy that
-- governed a decision can be read back from the system of record. Ch 5 adds a
-- rule and bumps versions in place.
CREATE TABLE compliance.policy_rule (
    rule_id      text NOT NULL,
    version      integer NOT NULL,
    title        text NOT NULL,
    body         text NOT NULL,
    params       jsonb NOT NULL DEFAULT '{}',
    synced_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (rule_id, version)
);

---------------------------------------------------------------- registry
CREATE TABLE registry.person (
    person_id      text PRIMARY KEY,
    full_name      text NOT NULL,
    date_of_birth  date NOT NULL
);

CREATE TABLE registry.ownership (
    person_id    text NOT NULL REFERENCES registry.person,
    business_id  text NOT NULL,
    pct          numeric(5,2) NOT NULL CHECK (pct > 0 AND pct <= 100),
    PRIMARY KEY (person_id, business_id)
);

---------------------------------------------------------------- relationship
CREATE TABLE relationship.banker (
    banker_id  text PRIMARY KEY,
    full_name  text NOT NULL,
    email      text NOT NULL UNIQUE
);

CREATE TABLE relationship.coverage (
    banker_id    text NOT NULL REFERENCES relationship.banker,
    business_id  text NOT NULL,
    PRIMARY KEY (banker_id, business_id)
);

CREATE TABLE relationship.note (
    note_id      text PRIMARY KEY,
    business_id  text NOT NULL,
    banker_id    text NOT NULL REFERENCES relationship.banker,
    written_on   date NOT NULL,
    body         text NOT NULL
);

---------------------------------------------------------------- indexes
CREATE INDEX ON ledger.deposit_account (business_id);
CREATE INDEX ON credit.facility (business_id);
CREATE INDEX ON credit.application (business_id);
CREATE INDEX ON compliance.adverse_event (business_id, occurred_on);
CREATE INDEX ON registry.ownership (business_id);
CREATE INDEX ON relationship.coverage (business_id);
CREATE INDEX ON relationship.note (business_id);
