"""Chapter 2: compile the Ossie model two ways and make them agree.

`build_cube_model` turns `semantic/brightwater.ossie.yaml` into Cube model
files. Cube serves the metrics; the policy engine and (from Ch 3) the agent's
tools ask Cube, never Postgres directly.

`reconcile` turns the same Ossie file straight into SQL, runs it on Postgres,
and compares the answers with Cube's. Two independent compilers of one
definition, checked against each other, is what lets us say the number the
agent saw is the number the definition specifies, not a stale cache or a
hand-edited model file.

The compiler handles the subset of Ossie this model uses: datasets with a
table or SQL source, fields with scalar expressions, dataset-scoped metrics,
and relationships. That is deliberate: it stays short enough to read in one sitting.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OSSIE_SCHEMA = ROOT / "semantic" / "vendor" / "ossie-schema.json"
TARGET_DIALECT = "POSTGRES"

CUBE_TYPES = {"String": "string", "Integer": "number", "Decimal": "number", "Float": "number",
              "Boolean": "boolean", "Date": "time", "DateTime": "time", "DateTimeTz": "time"}

# Words that look like identifiers in our expressions but are not columns.
_SQL_WORDS = {"and", "or", "not", "case", "when", "then", "else", "end", "null", "true", "false", "is", "in",
              "as", "between", "like", "distinct", "filter", "where"}
_IDENT = re.compile(r"(?<![\w.:'])([A-Za-z_][A-Za-z0-9_]*)(?![\w(])")
_STRING = re.compile(r"'(?:[^']|'')*'")


# ------------------------------------------------------------------ loading
def load(path: Path) -> dict[str, Any]:
    """Load the model and validate it against the vendored Ossie JSON Schema."""
    import jsonschema

    model = yaml.safe_load(path.read_text())
    schema = json.loads(OSSIE_SCHEMA.read_text())
    jsonschema.Draft202012Validator(schema).validate(model)
    return model


def expr(expression: dict[str, Any], dialect: str = TARGET_DIALECT) -> str:
    """The expression to run on our database: the dialect's form if given, else portable SQL."""
    for d in expression.get("dialects", []):
        if d["dialect"] == dialect:
            return d["expression"]
    return expression["sql"]


def _rewrite_identifiers(sql: str, fn) -> str:  # noqa: ANN001
    """Apply fn to every bare identifier outside string literals."""
    out, pos = [], 0
    for m in _STRING.finditer(sql):
        out.append(_IDENT.sub(lambda i: fn(i.group(1)), sql[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_IDENT.sub(lambda i: fn(i.group(1)), sql[pos:]))
    return "".join(out)


def _is_table(source: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][\w]*(\.[A-Za-z_][\w]*){0,2}", source.strip()) is not None


# ------------------------------------------------------------------ Ossie -> Cube
def _qualify_columns(sql: str) -> str:
    """Prefix source columns with {CUBE} so cubes stay unambiguous when joined."""
    return _rewrite_identifiers(sql, lambda w: w if w.lower() in _SQL_WORDS else f"{{CUBE}}.{w}")


def _field_refs(sql: str, fields: set[str]) -> str:
    """In a metric, a bare name is a field. Cube spells a member reference {name}."""
    return _rewrite_identifiers(sql, lambda w: f"{{{w}}}" if w in fields else w)


def to_cube(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    joins: dict[str, list[dict[str, Any]]] = {}
    for r in model.get("relationships", []):
        on = " AND ".join(f"{{CUBE}}.{a} = {{{r['to']}}}.{b}" for a, b in zip(r["from_columns"], r["to_columns"]))
        joins.setdefault(r["from"], []).append({"name": r["to"], "relationship": "many_to_one", "sql": on})

    cubes: dict[str, dict[str, Any]] = {}
    for ds in model["datasets"]:
        pk = set(ds.get("primary_key", []))
        fields = {f["name"] for f in ds.get("fields", [])}
        cube: dict[str, Any] = {"name": ds["name"], "description": " ".join(ds.get("description", "").split())}
        if _is_table(ds["source"]):
            cube["sql_table"] = ds["source"].strip()
        else:
            cube["sql"] = " ".join(ds["source"].split())
        if ds["name"] in joins:
            cube["joins"] = joins[ds["name"]]
        cube["dimensions"] = []
        for f in ds.get("fields", []):
            dim = {"name": f["name"], "sql": _qualify_columns(expr(f["expression"])),
                   "type": CUBE_TYPES.get(f.get("datatype", "String"), "string")}
            if f["name"] in pk:
                dim["primary_key"] = True
                dim["public"] = True
            if f.get("description"):
                dim["description"] = " ".join(f["description"].split())
            cube["dimensions"].append(dim)
        cube["measures"] = [
            {"name": m["name"], "type": "number", "sql": _field_refs(expr(m["expression"]), fields),
             **({"description": " ".join(m["description"].split())} if m.get("description") else {})}
            for m in ds.get("metrics", [])
        ]
        if not cube["measures"]:
            del cube["measures"]
        cubes[ds["name"]] = cube
    return cubes


def build_cube_model(model_path: Path, out_dir: Path) -> list[Path]:
    model = load(model_path)
    cubes_dir = out_dir / "cubes"
    cubes_dir.mkdir(parents=True, exist_ok=True)
    for stale in cubes_dir.glob("*.yml"):
        stale.unlink()
    header = (f"# GENERATED by `brightwater semantic build` from {model_path.name}. Do not edit;\n"
              f"# change the Ossie model and rebuild.\n")
    written = []
    for name, cube in to_cube(model).items():
        path = cubes_dir / f"{name}.yml"
        path.write_text(header + yaml.safe_dump({"cubes": [cube]}, sort_keys=False, width=110))
        written.append(path)
    return written


# ------------------------------------------------------------------ Ossie -> SQL (reconciliation)
def direct_sql(model: dict[str, Any], dataset: str, metrics: bool = True) -> str:
    """Compile one dataset straight to SQL: its metrics grouped by application, or its field rows."""
    ds = next(d for d in model["datasets"] if d["name"] == dataset)
    field_sql = {f["name"]: expr(f["expression"]) for f in ds.get("fields", [])}
    source = ds["source"].strip()
    frm = source if _is_table(source) else f"({source})"
    where = "WHERE application_id = ANY(CAST(:ids AS text[]))"
    if metrics:
        cols = [f"{_rewrite_identifiers(expr(m['expression']), lambda w: f'({field_sql[w]})' if w in field_sql else w)}"
                f" AS {m['name']}" for m in ds["metrics"]]
        return f"SELECT application_id, {', '.join(cols)} FROM {frm} AS src {where} GROUP BY application_id"
    cols = [f"{sql} AS {name}" for name, sql in field_sql.items()]
    return f"SELECT {', '.join(cols)} FROM {frm} AS src {where}"


def _norm(v: Any) -> Any:
    """Compare numbers as numbers whatever the transport (Cube returns strings)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}(T|$)", v):
        return v[:10]
    if isinstance(v, str) and v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return Decimal(str(v)).quantize(Decimal("0.000001"))
    except Exception:  # noqa: BLE001
        return v


def reconcile(model_path: Path, application_ids: list[str], verbose: bool = True) -> bool:
    """Check every field row and metric Cube serves against the same definition run on Postgres."""
    from . import db
    from .metrics_cube import Cube

    model = load(model_path)
    cube = Cube()
    all_ok = True
    with db.session() as conn:
        for ds in model["datasets"]:
            name = ds["name"]
            filters = [{"member": f"{name}.application_id", "operator": "equals", "values": application_ids}]
            if "application_id" not in {f["name"] for f in ds.get("fields", [])} or name == "application":
                continue
            ok, checked = True, 0

            # Field rows: the same set of rows, value for value.
            fields = [f["name"] for f in ds["fields"]]
            raw = db.rows(conn, direct_sql(model, name, metrics=False), ids=application_ids)
            served = cube.load({"dimensions": [f"{name}.{f}" for f in fields], "filters": filters, "limit": 50000})
            raw_set = {tuple(_norm(r[f]) for f in fields) for r in raw}
            served_set = {tuple(_norm(r[f"{name}.{f}"]) for f in fields) for r in served}
            if raw_set != served_set:
                ok = False
                print(f"  MISMATCH {name} rows: {sorted(map(str, raw_set ^ served_set))[:3]}")
            checked += len(raw) * len(fields)

            # Metrics: the same number per application.
            if ds.get("metrics"):
                metrics = [m["name"] for m in ds["metrics"]]
                raw = db.rows(conn, direct_sql(model, name), ids=application_ids)
                served = cube.load({"measures": [f"{name}.{m}" for m in metrics],
                                    "dimensions": [f"{name}.application_id"], "filters": filters})
                by_app = {r[f"{name}.application_id"]: r for r in served}
                for r in raw:
                    for m in metrics:
                        a = _norm(r[m])
                        b = _norm(by_app.get(r["application_id"], {}).get(f"{name}.{m}"))
                        if a != b:
                            ok = False
                            print(f"  MISMATCH {name}.{m} {r['application_id']}: postgres={a} cube={b}")
                checked += len(raw) * len(metrics)

            all_ok &= ok
            if verbose:
                print(f"{name:<20} {checked:>7,} values  {'ok' if ok else 'FAILED'}")
    print("reconciled: Cube matches Postgres" if all_ok else "NOT reconciled")
    return all_ok


# ------------------------------------------------------------------ impact of a definition change
def facts_direct(model: dict[str, Any], application_ids: list[str]) -> dict[str, Any]:
    """Policy facts for many applications at once, compiled straight from a model (no Cube).

    Used to ask "what would change?" about a model that is not deployed yet.
    """
    from . import db
    from .policy import Facts, RelatedFacility

    q = Decimal
    with db.session() as conn:
        prof = {r["application_id"]: r for r in db.rows(conn, direct_sql(model, "application_profile", False),
                                                         ids=application_ids)}
        dscr = {r["application_id"]: r["dscr"] for r in db.rows(conn, direct_sql(model, "application_month"),
                                                                 ids=application_ids)}
        expo = {r["application_id"]: r["group_exposure"] for r in db.rows(conn, direct_sql(model, "group_member"),
                                                                          ids=application_ids)}
        related: dict[str, list[Any]] = {}
        for r in sorted(db.rows(conn, direct_sql(model, "related_facility", False), ids=application_ids),
                        key=lambda r: r["facility_id"]):
            related.setdefault(r["application_id"], []).append(RelatedFacility(
                r["facility_id"], r["business_id"], q(r["owner_stake_pct"]).quantize(q("0.01")),
                int(r["days_past_due"])))
    out = {}
    for i in application_ids:
        p = prof[i]
        out[i] = Facts(
            application_id=i, kyc_current=bool(p["kyc_current"]),
            days_since_last_adverse_event=(None if p["days_since_last_adverse_event"] is None
                                           else int(p["days_since_last_adverse_event"])),
            dscr=q(dscr[i]).quantize(q("0.0001")), group_exposure=q(expo[i]).quantize(q("0.01")),
            related_facilities=tuple(related.get(i, [])), tenure_years=q(p["tenure_years"]).quantize(q("0.01")))
    return out


def impact(baseline_path: Path, candidate_path: Path) -> list[dict[str, Any]]:
    """Every application whose policy outcome differs between two versions of the model."""
    from . import db
    from .policy import evaluate

    with db.session() as conn:
        ids = [r[0] for r in conn.run("SELECT application_id FROM credit.application ORDER BY 1")]
    before = facts_direct(load(baseline_path), ids)
    after = facts_direct(load(candidate_path), ids)
    changes = []
    for i in ids:
        d1, d2 = evaluate(before[i]), evaluate(after[i])
        if (d1.outcome, d1.fired_ids) != (d2.outcome, d2.fired_ids):
            changes.append({"application_id": i, "before": d1.outcome.value, "after": d2.outcome.value,
                            "dscr_before": str(before[i].dscr), "dscr_after": str(after[i].dscr),
                            "fired_after": d2.fired_ids})
    return changes
