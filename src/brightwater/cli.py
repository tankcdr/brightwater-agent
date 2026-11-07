"""The `brightwater` command line.

Every chapter adds commands here. In Ch 3 the same commands become the
agent's tools, which is why each one prints JSON with --json and never asks
questions interactively.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import fixture, policy

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "fixture" / "out"


def _metrics_backend(name: str | None):
    name = name or os.getenv("BRIGHTWATER_METRICS", "cube")
    if name == "sql":
        from . import metrics_sql as backend
    elif name == "cube":
        from . import metrics_cube as backend
    else:
        raise SystemExit(f"unknown metrics backend {name!r} (sql or cube)")
    return name, backend


def cmd_fixture_generate(a: argparse.Namespace) -> None:
    ds = fixture.build(seed=a.seed, n=a.n)
    manifest = fixture.write_csv(ds, Path(a.out))
    total = sum(len(r) for r in ds.tables.values())
    print(f"wrote {len(manifest)} tables, {total:,} rows to {a.out}")
    print(f"hand-built: {', '.join(ds.handbuilt_ids)} (held out: {', '.join(ds.held_out_ids)})")


def cmd_fixture_break(a: argparse.Namespace) -> None:
    for line in fixture.break_fixture(Path(a.src), Path(a.out)):
        print(f"planted registry.ownership row: {line}")
    print(f"broken copy written to {a.out}")


def cmd_db_init(a: argparse.Namespace) -> None:
    from . import db
    with db.session() as conn:
        for name in db.init_schema(conn):
            print(f"applied {name}")


def cmd_db_load(a: argparse.Namespace) -> None:
    from . import db
    with db.session() as conn:
        counts = db.load_csv(conn, Path(a.src))
    for t, n in counts.items():
        print(f"{t:<28} {n:>8,}")


def cmd_policy_sync(a: argparse.Namespace) -> None:
    from . import db
    with db.session() as conn:
        for r in policy.RULES:
            conn.run(
                "INSERT INTO compliance.policy_rule (rule_id, version, title, body, params) "
                "VALUES (:i, :v, :t, :b, CAST(:p AS jsonb)) "
                "ON CONFLICT (rule_id, version) DO UPDATE SET title = EXCLUDED.title, body = EXCLUDED.body, "
                "params = EXCLUDED.params, synced_at = now()",
                i=r.rule_id, v=r.version, t=r.title, b=r.text, p=json.dumps(r.params))
    print(f"synced {len(policy.RULES)} rules ({policy.policy_version()})")


def _assess_one(app_id: str, backend_name: str | None) -> dict:
    name, backend = _metrics_backend(backend_name)
    if name == "sql":
        from . import db
        with db.session() as conn:
            facts = backend.facts_for(conn, app_id)
    else:
        facts = backend.facts_for(app_id)
    decision = policy.evaluate(facts)
    return {"metrics_backend": name, "facts": facts.to_dict(), "decision": decision.to_dict()}


def cmd_assess(a: argparse.Namespace) -> None:
    ids = list(fixture.load_expectations()) if a.handbuilt else a.application_ids
    if not ids:
        raise SystemExit("give one or more application ids, or --handbuilt")
    results = [_assess_one(i, a.metrics) for i in ids]
    if a.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2))
        return
    print(f"{'application':<12} {'outcome':<8} {'person':<6} {'DSCR':>7} {'group exp.':>12}  fired")
    for r in results:
        f, d = r["facts"], r["decision"]
        print(f"{d['application_id']:<12} {d['outcome']:<8} {'yes' if d['requires_person'] else 'no':<6} "
              f"{float(f['dscr']):>7.2f} {float(f['group_exposure']):>12,.0f}  "
              f"{','.join(x['rule_id'] for x in d['fired']) or '-'}")
    print(f"metrics: {results[0]['metrics_backend']}   policy: {results[0]['decision']['policy_version']}")


def cmd_graph_validate(a: argparse.Namespace) -> None:
    from . import ontology
    report = ontology.validate_dir(Path(a.src))
    print(report.summary())
    if not report.conforms:
        sys.exit(1)


def cmd_graph_load(a: argparse.Namespace) -> None:
    import asyncio

    from . import graph
    asyncio.run(graph.load(Path(a.src), skip_validation=a.skip_validation))


def cmd_graph_query(a: argparse.Namespace) -> None:
    import asyncio

    from . import graph
    out = asyncio.run(graph.run_named_query(a.name, a.params))
    print(json.dumps(out, indent=2, default=str))


def cmd_semantic_build(a: argparse.Namespace) -> None:
    from . import semantic
    written = semantic.build_cube_model(Path(a.model), Path(a.out))
    for p in written:
        print(f"wrote {p}")


def cmd_semantic_reconcile(a: argparse.Namespace) -> None:
    from . import semantic
    ok = semantic.reconcile(Path(a.model), a.application_ids or list(fixture.load_expectations()))
    sys.exit(0 if ok else 1)


def cmd_semantic_impact(a: argparse.Namespace) -> None:
    from collections import Counter

    from . import semantic
    changes = semantic.impact(Path(a.baseline), Path(a.model))
    for c in changes:
        print(f"{c['application_id']:<10} {c['before']:>8} -> {c['after']:<8} DSCR {float(c['dscr_before']):.2f} -> "
              f"{float(c['dscr_after']):.2f}  fired {','.join(c['fired_after']) or '-'}")
    moves = Counter((c["before"], c["after"]) for c in changes)
    print(f"{len(changes)} application(s) change: " + ", ".join(f"{n} {b}->{a}" for (b, a), n in moves.most_common()))


def _app_ids(a: argparse.Namespace) -> list[str]:
    ids = list(fixture.load_expectations()) if getattr(a, "handbuilt", False) else a.application_ids
    if not ids:
        raise SystemExit("give one or more application ids, or --handbuilt")
    return ids


def cmd_tool(a: argparse.Namespace) -> None:
    from . import tools
    ctx = tools.RunContext(application_id=a.application_id or "", configuration="cli")
    fn = tools.TOOLS[a.name]
    out = fn(ctx) if a.name == "get_policy" else fn(ctx, a.application_id)
    print(json.dumps(out, indent=2, default=str))


def cmd_agent_run(a: argparse.Namespace) -> None:
    from . import agent, bench
    for app_id in _app_ids(a):
        for i in range(a.repeat):
            model = None
            name = a.model or agent.default_model()
            if name == "scripted":
                from .scripted import ScriptedAnalyst
                model = ScriptedAnalyst(application_id=app_id)
            ctx = agent.run(app_id, configuration=a.configuration, model=model, model_name=name,
                            use_memory=not a.no_memory)
            row = bench.record(ctx) if name != "scripted" or a.record else None
            d = ctx.decision or {}
            print(f"{app_id} run {i + 1}: {d.get('outcome', 'NOT FILED'):<8} lint rejections {ctx.lint_rejections} "
                  f"tools {'>'.join(ctx.tool_calls)}" + (f"  {row['wall_s']}s ${row['cost_usd'] or 0:.4f}" if row else ""))


def _answerer(name: str | None):
    from . import agent, procedure
    name = name or agent.default_model()
    if name == "scripted":
        return procedure.ScriptedAnswerer()
    return procedure.ModelAnswerer(agent.chat_model(name), name)


def cmd_procedure_run(a: argparse.Namespace) -> None:
    from . import bench, procedure
    person = procedure.auto_person(a.auto_confirm) if a.auto_confirm else procedure.console_person
    for app_id in _app_ids(a):
        for i in range(a.repeat):
            r = procedure.run(app_id, _answerer(a.model), person, configuration=a.configuration,
                              skill=a.skill, decision_client=_decision_client(a.decision_model))
            row = None
            if a.model != "scripted" or a.record:
                row = bench.record(r.ctx, extra={"aip_run_id": r.aip_run_id, "human_pauses": r.human_pauses,
                                                 "decision_pauses": r.decision_pauses, "review_pauses": r.review_pauses})
            d = r.ctx.decision or {"outcome": "NOT FILED", "status": "-"}
            print(f"{app_id} run {i + 1}: {d['outcome']:<8} {d['status']:<15} aip {r.aip_run_id}  "
                  f"steps {'>'.join(r.steps)}" + (f"  {row['wall_s']}s" if row else ""))


def _decision_client(name: str | None):
    if not name:
        return None
    from . import resolver
    return resolver.client_for(name)


def cmd_procedure_build(a: argparse.Namespace) -> None:
    from . import procedure
    for p in procedure.build():
        print(f"wrote {p}")


def cmd_procedure_impact(a: argparse.Namespace) -> None:
    from collections import Counter

    from . import procedure
    out = procedure.impact(a.old, a.new)
    if a.json:
        print(json.dumps(out, indent=2))
        return
    print(f"policy {out['old']} -> {out['new']}")
    for c in out["changes"]:
        print(f"{c['application_id']:<10} {c['outcome'][0]:>8} -> {c['outcome'][1]:<8} person {c['requires_person'][0]!s:>5} -> "
              f"{c['requires_person'][1]!s:<5} fired {','.join(c['fired'][1])}  exposure {float(c['group_exposure']):,.0f}")
    kinds = Counter("outcome" if c["outcome"][0] != c["outcome"][1] else "person only" for c in out["changes"])
    print(f"{len(out['changes'])} application(s) change ({dict(kinds)}); "
          f"{len(out['runs_pinned_to_old'])} recorded run(s) stay pinned to the old revision")


def cmd_dm(a: argparse.Namespace) -> None:
    from . import decision_model as dm
    from . import resolver
    if a.sub == "dataset":
        rows = dm.build_states()
        dm.write_jsonl(dm.DATA / "states.jsonl", rows)
        print({s: sum(r["split"] == s for r in rows) for s in ("train", "valid", "test")})
    elif a.sub == "label":
        print(dm.label_all(a.labeller, a.limit))
    elif a.sub == "spot-check":
        print(dm.spot_check(a.n))
    elif a.sub == "export":
        print(dm.export())
    elif a.sub == "calibrate":
        print(json.dumps(dm.calibrate(resolver.client_for(f"local:{a.url}")), indent=2))
    elif a.sub == "evaluate":
        opts = {}
        for o in a.options.split(","):
            if o == "A":
                from . import agent, procedure
                ans = procedure.ModelAnswerer(agent.chat_model(a.driver), a.driver)
                opts["A"] = lambda state, qs, _a=ans: _a.decide(type("P", (), {"state": state, "questions": qs})())
            elif o == "B":
                from typesafe_sdk import TypeSafeClient
                opts["B"] = TypeSafeClient()
            elif o == "C":
                opts["C"] = resolver.client_for(f"frontier:{a.driver}")
            elif o == "D":
                opts["D"] = resolver.client_for(f"local:{a.url}")
        print(json.dumps(dm.evaluate(opts, repeats=a.repeats, limit=a.limit), indent=2))


def cmd_aip_server(a: argparse.Namespace) -> None:
    from . import aip_patch
    aip_patch.serve(a.args)


def cmd_netwatch(a: argparse.Namespace) -> None:
    from . import netwatch
    cmd = a.command[1:] if a.command and a.command[0] == "--" else a.command
    sys.exit(netwatch.run(cmd))


def cmd_auth(a: argparse.Namespace) -> None:
    import getpass

    from . import identity
    if a.sub == "login":
        c = identity.login(a.username, a.password or getpass.getpass(f"password for {a.username}: "))
        print(f"signed in as {c.get('preferred_username')} roles {(c.get('realm_access') or {}).get('roles', [])}")
    else:
        print(json.dumps(identity.current_user(), indent=2))


def cmd_authz(a: argparse.Namespace) -> None:
    from . import db, identity
    if a.sub == "sync":
        print(identity.fga_sync())
    elif a.sub == "rls-on":
        with db.session() as conn:
            conn.run((db.SCHEMA_DIR / "900_enable_rls.sql").read_text())
        print("row-level security enabled for brightwater_app")
    elif a.sub == "check":
        print(identity.can_assess(a.username, a.application_id))


def cmd_results_table(a: argparse.Namespace) -> None:
    from . import bench
    print(bench.table())


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["aip-server"]:
        # Everything after the subcommand belongs to `aip server`. argparse's REMAINDER does not
        # capture a first argument that starts with "--" on Python 3.14, so hand it over untouched.
        from . import aip_patch
        aip_patch.serve(argv[1:])
        return
    p = argparse.ArgumentParser(prog="brightwater", description="Brightwater loan assessment tutorial")
    sub = p.add_subparsers(dest="cmd", required=True)

    fx = sub.add_parser("fixture", help="synthetic systems of record").add_subparsers(dest="sub", required=True)
    g = fx.add_parser("generate", help="write the seeded fixture as CSV")
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--n", type=int, default=1200, help="generated applicants (hand-built ones are always added)")
    g.add_argument("--out", default=str(DEFAULT_OUT))
    g.set_defaults(fn=cmd_fixture_generate)
    br = fx.add_parser("break", help="copy a fixture and plant two cross-record defects (Ch 1 proof)")
    br.add_argument("--from", dest="src", default=str(DEFAULT_OUT))
    br.add_argument("--out", default=str(ROOT / "fixture" / "broken"))
    br.set_defaults(fn=cmd_fixture_break)

    dbp = sub.add_parser("db", help="Postgres systems of record").add_subparsers(dest="sub", required=True)
    dbp.add_parser("init", help="create the five schemas and RLS policies").set_defaults(fn=cmd_db_init)
    ld = dbp.add_parser("load", help="load the fixture CSVs")
    ld.add_argument("--from", dest="src", default=str(DEFAULT_OUT))
    ld.set_defaults(fn=cmd_db_load)

    pol = sub.add_parser("policy", help="lending policy").add_subparsers(dest="sub", required=True)
    pol.add_parser("sync", help="write policy.py to compliance.policy_rule").set_defaults(fn=cmd_policy_sync)

    asp = sub.add_parser("assess", help="compute facts and apply the policy")
    asp.add_argument("application_ids", nargs="*")
    asp.add_argument("--handbuilt", action="store_true", help="assess the six hand-built applicants")
    asp.add_argument("--metrics", choices=["sql", "cube"], help="where facts come from (default: $BRIGHTWATER_METRICS or cube)")
    asp.add_argument("--json", action="store_true")
    asp.set_defaults(fn=cmd_assess)

    gr = sub.add_parser("graph", help="ontology and memory graph").add_subparsers(dest="sub", required=True)
    v = gr.add_parser("validate", help="SHACL-validate a fixture directory")
    v.add_argument("--from", dest="src", default=str(DEFAULT_OUT))
    v.set_defaults(fn=cmd_graph_validate)
    gl = gr.add_parser("load", help="validate, then load entities and typed edges into Neo4j")
    gl.add_argument("--from", dest="src", default=str(DEFAULT_OUT))
    gl.add_argument("--skip-validation", action="store_true", help="for demonstrating why you should not")
    gl.set_defaults(fn=cmd_graph_load)
    gq = gr.add_parser("query", help="run a numbered query from queries/")
    gq.add_argument("name")
    gq.add_argument("--param", dest="params", action="append", default=[], metavar="KEY=VALUE")
    gq.set_defaults(fn=cmd_graph_query)

    se = sub.add_parser("semantic", help="semantic layer").add_subparsers(dest="sub", required=True)
    sb = se.add_parser("build", help="compile the Ossie model into Cube model files")
    sb.add_argument("--model", default=str(ROOT / "semantic" / "brightwater.ossie.yaml"))
    sb.add_argument("--out", default=str(ROOT / "semantic" / "cube" / "model"))
    sb.set_defaults(fn=cmd_semantic_build)
    sr = se.add_parser("reconcile", help="check Cube's answers against raw Postgres")
    sr.add_argument("application_ids", nargs="*")
    sr.add_argument("--model", default=str(ROOT / "semantic" / "brightwater.ossie.yaml"))
    sr.set_defaults(fn=cmd_semantic_reconcile)

    si = se.add_parser("impact", help="which applications change outcome under a candidate model")
    si.add_argument("--model", required=True, help="the candidate Ossie model")
    si.add_argument("--baseline", default=str(ROOT / "semantic" / "brightwater.ossie.yaml"))
    si.set_defaults(fn=cmd_semantic_impact)

    tl = sub.add_parser("tool", help="call one agent tool and print its JSON (what the agent sees)")
    tl.add_argument("name", choices=["get_application", "get_figures", "get_related_facilities", "find_connections",
                                     "get_notes", "get_policy"])
    tl.add_argument("application_id", nargs="?")
    tl.set_defaults(fn=cmd_tool)

    ag = sub.add_parser("agent", help="the free-form agent (Ch 3)").add_subparsers(dest="sub", required=True)
    ar = ag.add_parser("run", help="assess applications with the agent, recording every run")
    ar.add_argument("application_ids", nargs="*")
    ar.add_argument("--handbuilt", action="store_true")
    ar.add_argument("--repeat", type=int, default=1)
    ar.add_argument("--model", help="model id, or 'scripted' for a no-API dry run (default $BRIGHTWATER_DRIVER_MODEL)")
    ar.add_argument("--configuration", default="ch3-freeform")
    ar.add_argument("--no-memory", action="store_true", help="skip the Neo4j memory middleware and trace")
    ar.add_argument("--record", action="store_true", help="also record scripted runs in results/runs.jsonl")
    ar.set_defaults(fn=cmd_agent_run)

    pr = sub.add_parser("procedure", help="the compiled AIP procedure (Ch 4 onward)").add_subparsers(dest="sub", required=True)
    pb = pr.add_parser("build", help="refresh the skill's policy asset and source material from the repo")
    pb.set_defaults(fn=cmd_procedure_build)
    pr.add_parser("export-traces", help="summarize recorded Ch 3 runs into the skill's source/").set_defaults(
        fn=lambda a: print(__import__("brightwater.procedure", fromlist=["x"]).export_traces()))
    prr = pr.add_parser("run", help="run the procedure on applications, recording every run")
    prr.add_argument("application_ids", nargs="*")
    prr.add_argument("--handbuilt", action="store_true")
    prr.add_argument("--repeat", type=int, default=1)
    prr.add_argument("--model", help="driving model id, or 'scripted' (default $BRIGHTWATER_DRIVER_MODEL)")
    prr.add_argument("--configuration", default="ch4-procedure")
    prr.add_argument("--skill", help="a skill folder or published name[@revision] (default: the server's, else the repo folder)")
    prr.add_argument("--decision-model", help="Ch 6: answer decisions with a resolver: frontier:<model> or local:<url>")
    prr.add_argument("--auto-confirm", metavar="USER", help="answer person-only steps as USER (tests and demos only)")
    prr.add_argument("--record", action="store_true", help="also record scripted runs")
    prr.set_defaults(fn=cmd_procedure_run)

    pi = pr.add_parser("impact", help="which applications change between two procedure revisions (Ch 5)")
    pi.add_argument("old", help="old revision: a skill folder or name@revision")
    pi.add_argument("new", help="new revision: a skill folder or name@revision")
    pi.add_argument("--json", action="store_true")
    pi.set_defaults(fn=cmd_procedure_impact)

    dmp = sub.add_parser("dm", help="the decision model (Ch 6)").add_subparsers(dest="sub", required=True)
    dmp.add_parser("dataset", help="assess-step states for every application, split by application").set_defaults(fn=cmd_dm)
    dl = dmp.add_parser("label", help="label every state against the rubric")
    dl.add_argument("--labeller", default="frontier:claude-opus-5-5", help="frontier:<model> or rubric")
    dl.add_argument("--limit", type=int)
    dl.set_defaults(fn=cmd_dm)
    ds = dmp.add_parser("spot-check", help="a person checks a random sample of labels")
    ds.add_argument("--n", type=int, default=30)
    ds.set_defaults(fn=cmd_dm)
    dmp.add_parser("export", help="chat-format JSONL for fine-tuning").set_defaults(fn=cmd_dm)
    dc = dmp.add_parser("calibrate", help="fit temperatures on the validation split against the served model")
    dc.add_argument("--url", default="http://localhost:8081/v1")
    dc.set_defaults(fn=cmd_dm)
    de = dmp.add_parser("evaluate", help="compare decision model options on the held-out split")
    de.add_argument("--options", default="A,C,D", help="comma list of A,B,C,D")
    de.add_argument("--driver", default="claude-opus-5-5", help="model for options A and C")
    de.add_argument("--url", default="http://localhost:8081/v1")
    de.add_argument("--repeats", type=int, default=3)
    de.add_argument("--limit", type=int)
    de.set_defaults(fn=cmd_dm)

    asv = sub.add_parser("aip-server", help="`aip server` with the resolver patch applied (Ch 6)")
    asv.add_argument("args", nargs=argparse.REMAINDER)
    asv.set_defaults(fn=cmd_aip_server)

    nw = sub.add_parser("netwatch", help="run a command with no API keys and record every remote endpoint (Ch 7)")
    nw.add_argument("command", nargs=argparse.REMAINDER)
    nw.set_defaults(fn=cmd_netwatch)

    au = sub.add_parser("auth", help="sign in with Keycloak (Ch 8)").add_subparsers(dest="sub", required=True)
    al = au.add_parser("login")
    al.add_argument("username")
    al.add_argument("--password")
    al.set_defaults(fn=cmd_auth)
    au.add_parser("whoami").set_defaults(fn=cmd_auth)
    az = sub.add_parser("authz", help="entitlements and row-level security (Ch 8)").add_subparsers(dest="sub", required=True)
    az.add_parser("sync", help="write the OpenFGA model and coverage tuples").set_defaults(fn=cmd_authz)
    az.add_parser("rls-on", help="enable row-level security for the agent's role").set_defaults(fn=cmd_authz)
    ac = az.add_parser("check", help="may USER assess APPLICATION?")
    ac.add_argument("username")
    ac.add_argument("application_id")
    ac.set_defaults(fn=cmd_authz)

    rs = sub.add_parser("results", help="the results table").add_subparsers(dest="sub", required=True)
    rs.add_parser("table", help="rebuild results/RESULTS.md from results/runs.jsonl").set_defaults(fn=cmd_results_table)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
