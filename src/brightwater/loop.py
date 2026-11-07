"""Chapter 10: close the loop.

A scheduled job (`brightwater dm cycle`, weekly by launchd or cron) does one
cycle:

1. Pull every human answer given at a review pause since the last cycle. These
   are the cases the model was least sure of, answered by the people the
   policy trusts, so they are the most valuable labels there are.
2. Rebuild the fine-tuning data with those answers over the rubric labels.
3. Train a candidate.
4. Serve it and evaluate it against the current model on the held-out split.
5. Promote it only if agreement holds and calibration does not get worse.
   Otherwise record the rejection and why.

Every cycle, promoted or not, is a line in `decision_model/registry.jsonl`.
The version of the model that answered is already on every decision it made:
in the AIP attribution (`model`) and in `agent.decision.answered_by`.
"""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import decision_model as dm
from . import resolver

ROOT = Path(__file__).resolve().parents[2]
AGREEMENT_SLACK = 0.005   # a candidate may be at most half a point less in agreement
ECE_SLACK = 0.02          # and at most two points worse calibrated


def registry_path() -> Path:
    return dm.ART.parent / "registry.jsonl"


def registry() -> list[dict[str, Any]]:
    return dm.read_jsonl(registry_path())


def current() -> dict[str, Any] | None:
    return next((r for r in reversed(registry()) if r["status"] == "promoted"), None)


def _mean(d: dict[str, Any]) -> float:
    vals = [v for v in d.values() if v is not None]
    return round(statistics.fmean(vals), 4) if vals else 1.0


def new_review_answers(since: str | None) -> list[dict[str, Any]]:
    from .procedure import review_answers_path
    rows = dm.read_jsonl(review_answers_path())
    return [r for r in rows if r.get("answered_by", "").startswith("user:") and (since is None or r["at"] > since)]


def mlx_trainer(candidate_dir: Path) -> str:
    """Train on Apple Silicon with the same script Ch 6 uses, into a candidate adapter folder."""
    # Training progress goes to stderr: stdout is the cycle's JSON (`brightwater dm cycle | tee x.json`).
    subprocess.run(["bash", str(ROOT / "decision_model" / "train_mlx.sh"), "train"], check=True, stdout=sys.stderr,
                   env={**__import__("os").environ, "BRIGHTWATER_DM_ADAPTERS": str(candidate_dir)})
    # train_mlx.sh writes the candidate's metadata next to its adapters, never over the live model's.
    return json.loads((candidate_dir / "model.json").read_text())["version"]


def cycle(train: Callable[[Path], str], serve: Callable[[str, Path | None], Any],
          limit: int | None = None) -> dict[str, Any]:
    """One retrain, evaluate, promote-or-reject cycle. `serve(version, adapters)` returns a decision client."""
    cur = current() or _register_chapter6_model()
    since = cur["created_at"] if cur else None
    answers = new_review_answers(since)
    sizes = dm.export()
    candidate_dir = dm.ART / "candidates" / time.strftime("%Y%m%dT%H%M%S")
    candidate_dir.mkdir(parents=True, exist_ok=True)
    version = train(candidate_dir)
    candidate = serve(version, candidate_dir)
    if isinstance(candidate, resolver.EndpointDecisionClient):
        # Calibrate the candidate on the validation split before it is compared with anything.
        dm.calibrate(candidate, out=candidate_dir / "calibration.json")
        candidate.calibration = json.loads((candidate_dir / "calibration.json").read_text())["temperature"]
    options = {"candidate": candidate}
    if cur:
        options["current"] = serve(cur["version"], None)
    try:
        res = dm.evaluate(options, repeats=1, limit=limit)
    finally:
        for client in options.values():
            proc = getattr(client, "_proc", None)
            if proc is not None:
                proc.terminate()
    cand = res["candidate"]
    entry: dict[str, Any] = {"version": version, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "review_answers_used": len(answers), "training_rows": sizes,
                             "agreement": _mean(cand["agreement"]), "ece": _mean(cand["ece"])}
    if cur:
        base = res["current"]
        a_ok = entry["agreement"] >= _mean(base["agreement"]) - AGREEMENT_SLACK
        e_ok = entry["ece"] <= _mean(base["ece"]) + ECE_SLACK
        entry["against"] = {"version": cur["version"], "agreement": _mean(base["agreement"]), "ece": _mean(base["ece"])}
        if a_ok and e_ok:
            entry.update(status="promoted", reason="agreement and calibration held")
        else:
            why = [] if a_ok else [f"agreement {entry['agreement']} < {entry['against']['agreement']} - {AGREEMENT_SLACK}"]
            why += [] if e_ok else [f"ECE {entry['ece']} > {entry['against']['ece']} + {ECE_SLACK}"]
            entry.update(status="rejected", reason="; ".join(why))
    else:
        entry.update(status="promoted", reason="first model")
    if entry["status"] == "promoted":
        live = dm.ART / "adapters"
        if live.exists():
            shutil.rmtree(live)
        shutil.copytree(candidate_dir, live)
        if (candidate_dir / "calibration.json").exists():
            shutil.copyfile(candidate_dir / "calibration.json", dm.ART / "calibration.json")
        meta = json.loads((dm.ART / "model.json").read_text()) if (dm.ART / "model.json").exists() else {}
        if (candidate_dir / "model.json").exists():
            meta.update(json.loads((candidate_dir / "model.json").read_text()))
        (dm.ART / "model.json").write_text(json.dumps({**meta, "version": version, "served_name": "default_model"},
                                                      indent=2) + "\n")
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def _register_chapter6_model() -> dict[str, Any] | None:
    """The model trained in Chapter 6 is production before any cycle has run. Record it as the first
    registry entry, so the first cycle's candidate is compared with it rather than promoted unopposed."""
    meta_path = dm.ART / "model.json"
    if registry() or not meta_path.exists() or not (dm.ART / "adapters").exists():
        return None
    meta = json.loads(meta_path.read_text())
    entry = {"version": meta["version"], "created_at": meta.get("trained_at")
             or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "status": "promoted", "reason": "Chapter 6 model, registered at the first cycle"}
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def mlx_server(version: str, adapters: Path | None, port: int | None = None) -> Any:
    """Serve one adapter set with mlx_lm.server on its own free port, so candidate and current never collide."""
    import httpx
    port = port or _free_port()
    args = ["mlx_lm.server", "--model", json.loads((dm.ART / "model.json").read_text())["base"],
            "--adapter-path", str(adapters or dm.ART / "adapters"), "--port", str(port)]
    proc = subprocess.Popen(args, stdout=sys.stderr)
    for _ in range(120):
        try:
            httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    client = resolver.EndpointDecisionClient(f"http://127.0.0.1:{port}/v1", "default_model", version=version,
                                             calibration=json.loads((dm.ART / "calibration.json").read_text())
                                             .get("temperature", {}) if (dm.ART / "calibration.json").exists() else {})
    client._proc = proc  # type: ignore[attr-defined]
    return client
