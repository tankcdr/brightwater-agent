// 03. Did repeated runs on one applicant take the same steps?
//
// One row per run: the tools it called, in order, and what it filed. In Ch 3
// (free-form) expect different orders across repeats; in Ch 4 (compiled
// procedure) expect one order.
//
//   brightwater graph query 03 --param app=APP-H4

MATCH (rt:ReasoningTrace {application_id: $app})-[hs:HAS_STEP]->(:ReasoningStep)-[:USES_TOOL]->(:ToolCall)-[:INSTANCE_OF]->(t:Tool)
WITH rt, hs.order AS step, t.name AS tool
ORDER BY rt.started_at, step
WITH rt, collect(tool) AS sequence
OPTIONAL MATCH (rt)-[:PRODUCED]->(d:Decision)
RETURN rt.configuration AS configuration, rt.session_id AS run_id, sequence,
       size(sequence) AS steps, d.outcome AS outcome
ORDER BY configuration, run_id
