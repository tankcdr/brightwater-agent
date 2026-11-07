-- Filed decisions (Ch 3 onward).
--
-- A decision is written by the agent's file_decision tool after the rationale
-- linter passes. It is the row every later chapter measures: the outcome, the
-- records it cites, which configuration produced it, and who answered.

DROP SCHEMA IF EXISTS agent CASCADE;
CREATE SCHEMA agent;

CREATE TABLE agent.decision (
    decision_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id    text NOT NULL,
    outcome           text NOT NULL CHECK (outcome IN ('approve', 'review', 'decline')),
    status            text NOT NULL CHECK (status IN ('filed', 'pending_person')),
    requires_person   boolean NOT NULL,
    rationale         text NOT NULL,
    cited_records     jsonb NOT NULL,
    records_read      jsonb,              -- every record key the run's tools returned
    assessment        jsonb,              -- risk tier, cash flow stability, recommendation (when given)
    configuration     text NOT NULL,      -- e.g. ch3-freeform, ch4-procedure
    run_id            text NOT NULL,      -- the agent run, or the AIP run id from Ch 4
    trace_id          text,               -- OpenTelemetry trace id
    answered_by       text NOT NULL,      -- who answered the judgment: agent:<model>, model:<resolver>/<version>, user:<subject>
    confirmed_by      text,               -- the person who confirmed a P6/P7 decision
    semantic_version  text NOT NULL,      -- hash of the Ossie model that produced the figures
    policy_version    text NOT NULL,
    filed_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON agent.decision (application_id, filed_at);
CREATE INDEX ON agent.decision (configuration);
