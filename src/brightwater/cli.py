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
    name = name or os.getenv("BRIGHTWATER_METRICS", "sql")
    if name == "sql":
        from . import metrics_sql as backend
    else:
        raise SystemExit(f"unknown metrics backend {name!r} (sql; Ch 2 adds cube)")
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


def main(argv: list[str] | None = None) -> None:
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
    asp.add_argument("--metrics", choices=["sql"], help="where facts come from (default: $BRIGHTWATER_METRICS or sql)")
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

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
