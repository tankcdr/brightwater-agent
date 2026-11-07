"""Chapter 8: who is asking, and may they?

* **Identity.** Bankers sign in to Keycloak (`brightwater auth login`). The
  token's `preferred_username` is the banker; its realm roles say what they may
  do (`banker`, `procedure_author`).
* **Entitlement.** OpenFGA answers "may this user assess this application?":
  a user may if they cover the applicant business. Tuples are synced from
  `relationship.coverage` (`brightwater authz sync`).
* **Policy.** OPA decides tool calls (here, before every tool) and catalog
  writes (at agentgateway, before the AIP server).
* **Row-level security.** With enforcement on, the tools connect to Postgres
  as `brightwater_app` with the banker set on the session, so the RLS
  policies from Ch 0 apply (`brightwater authz rls-on` switches them on).

Everything is off until `BRIGHTWATER_ENFORCE=on`, so Ch 0 to 7 run unchanged.
Every decision taken is written to `audit.authz`, which the Ch 9 packet reads.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

TOKEN_FILE = Path(os.getenv("BRIGHTWATER_HOME", Path.home() / ".brightwater")) / "token.json"
FGA_FILE = TOKEN_FILE.parent / "openfga.json"


def enforced() -> bool:
    return os.getenv("BRIGHTWATER_ENFORCE", "off") == "on"


def _url(name: str, default: str) -> str:
    return os.getenv(name, default).rstrip("/")


# ------------------------------------------------------------------ identity
def login(username: str, password: str) -> dict[str, Any]:
    kc = _url("BRIGHTWATER_KEYCLOAK_URL", "http://localhost:8180")
    r = httpx.post(f"{kc}/realms/brightwater/protocol/openid-connect/token",
                   data={"grant_type": "password", "client_id": "brightwater-cli", "username": username,
                         "password": password, "scope": "openid"}, timeout=15)
    r.raise_for_status()
    tok = r.json()
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tok))
    TOKEN_FILE.chmod(0o600)
    return claims(tok["access_token"])


def access_token() -> str | None:
    if os.getenv("BRIGHTWATER_TOKEN"):
        return os.environ["BRIGHTWATER_TOKEN"]
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())["access_token"]
    return None


def _issuer() -> str:
    return os.getenv("BRIGHTWATER_TOKEN_ISSUER", f"{_url('BRIGHTWATER_KEYCLOAK_URL', 'http://localhost:8180')}/realms/brightwater")


def claims(token: str | None = None) -> dict[str, Any]:
    """The token's claims.

    With enforcement on, the signature, expiry and issuer are verified against Keycloak's published keys,
    so an edited or expired token identifies nobody. With enforcement off the claims are only read, for
    `auth whoami`.
    """
    import jwt

    token = token or access_token()
    if not token:
        return {}
    if not enforced():
        return jwt.decode(token, options={"verify_signature": False})
    kc = _url("BRIGHTWATER_KEYCLOAK_URL", "http://localhost:8180")
    key = jwt.PyJWKClient(f"{kc}/realms/brightwater/protocol/openid-connect/certs").get_signing_key_from_jwt(token)
    try:
        return jwt.decode(token, key.key, algorithms=["RS256"], issuer=_issuer(), options={"verify_aud": False})
    except jwt.InvalidTokenError as e:
        log("keycloak", "unknown", "authenticate", "token", False, {"error": str(e)})
        return {}


def current_user() -> dict[str, Any]:
    c = claims()
    return {"username": c.get("preferred_username"), "roles": (c.get("realm_access") or {}).get("roles", []),
            "subject": f"user:{c['preferred_username']}" if c.get("preferred_username") else None,
            "expires": c.get("exp")}


def banker_id(username: str | None) -> str | None:
    if not username:
        return None
    from . import db
    with db.session() as conn:
        rows = conn.run("SELECT banker_id FROM relationship.banker WHERE split_part(email, '@', 1) = :u", u=username)
    return rows[0][0] if rows else None


# ------------------------------------------------------------------ audit log
def log(engine: str, subject: str, action: str, obj: str, allowed: bool, detail: dict[str, Any] | None = None) -> None:
    from . import db
    try:
        with db.session() as conn:
            conn.run("INSERT INTO audit.authz (decision_id, engine, subject, action, object, allowed, detail) "
                     "VALUES (:d, :e, :s, :a, :o, :ok, CAST(:x AS jsonb))",
                     d=os.getenv("BRIGHTWATER_DECISION_ID"), e=engine, s=subject, a=action, o=obj, ok=allowed,
                     x=json.dumps(detail or {}))
    except Exception:
        # With enforcement on, a check that cannot be recorded does not pass: the caller sees the error
        # and fails closed. With enforcement off nothing depends on the log.
        if enforced():
            raise


# ------------------------------------------------------------------ OpenFGA
def _fga() -> tuple[str, str]:
    url = _url("BRIGHTWATER_OPENFGA_URL", "http://localhost:8082")
    store = os.getenv("BRIGHTWATER_OPENFGA_STORE") or (json.loads(FGA_FILE.read_text())["store_id"] if FGA_FILE.exists() else "")
    return url, store


def fga_sync() -> dict[str, Any]:
    """Create a store, write the model, and write one tuple per coverage row and per application."""
    from . import db

    url = _url("BRIGHTWATER_OPENFGA_URL", "http://localhost:8082")
    model = json.loads((Path(__file__).resolve().parents[2] / "policy" / "openfga" / "model.json").read_text())
    with httpx.Client(timeout=30) as h:
        store = h.post(f"{url}/stores", json={"name": "brightwater"}).json()["id"]
        model_id = h.post(f"{url}/stores/{store}/authorization-models", json=model).json()["authorization_model_id"]
        with db.session() as conn:
            cov = conn.run("SELECT split_part(b.email, '@', 1), c.business_id FROM relationship.coverage c "
                           "JOIN relationship.banker b USING (banker_id)")
            apps = conn.run("SELECT application_id, business_id FROM credit.application")
        tuples = [{"user": f"user:{u}", "relation": "coverer", "object": f"business:{b}"} for u, b in cov]
        tuples += [{"user": f"business:{b}", "relation": "applicant", "object": f"application:{a}"} for a, b in apps]
        for i in range(0, len(tuples), 100):  # OpenFGA accepts at most 100 writes per request
            h.post(f"{url}/stores/{store}/write", json={"writes": {"tuple_keys": tuples[i:i + 100]},
                                                        "authorization_model_id": model_id}).raise_for_status()
    FGA_FILE.parent.mkdir(parents=True, exist_ok=True)
    FGA_FILE.write_text(json.dumps({"store_id": store, "model_id": model_id}))
    return {"store_id": store, "model_id": model_id, "tuples": len(tuples)}


def can_assess(username: str, application_id: str) -> bool:
    url, store = _fga()
    t0 = time.perf_counter()
    r = httpx.post(f"{url}/stores/{store}/check", json={"tuple_key": {
        "user": f"user:{username}", "relation": "can_assess", "object": f"application:{application_id}"}}, timeout=10)
    r.raise_for_status()
    ok = bool(r.json().get("allowed"))
    log("openfga", f"user:{username}", "can_assess", f"application:{application_id}", ok,
        {"ms": int((time.perf_counter() - t0) * 1000)})
    return ok


# ------------------------------------------------------------------ OPA
def tool_allowed(tool: str, application_id: str | None) -> tuple[bool, str]:
    """OPA's decision for one tool call, given the user's roles and their entitlement."""
    user = current_user()
    if not user["username"]:
        return False, "no signed-in user (brightwater auth login)"
    entitled = can_assess(user["username"], application_id) if application_id else False
    r = httpx.post(f"{_url('BRIGHTWATER_OPA_URL', 'http://localhost:8181')}/v1/data/brightwater/tool_allow",
                   json={"input": {"tool": tool, "roles": user["roles"], "entitled": entitled,
                                   "user": user["username"], "application_id": application_id}}, timeout=10)
    r.raise_for_status()
    ok = bool(r.json().get("result"))
    log("opa", user["subject"], tool, f"application:{application_id}", ok, {"entitled": entitled, "roles": user["roles"]})
    return ok, "" if ok else f"{user['username']} may not call {tool} for {application_id}"


def require_assess(application_id: str) -> str:
    """Fail closed unless the signed-in user may assess this application. Returns the username."""
    user = current_user()
    if not user["username"]:
        raise PermissionError("no signed-in user: run `brightwater auth login`")
    if not can_assess(user["username"], application_id):
        raise PermissionError(f"{user['username']} is not entitled to assess {application_id}")
    return user["username"]
