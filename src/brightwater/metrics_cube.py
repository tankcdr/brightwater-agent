"""Chapter 2: the policy facts, read from the semantic layer.

Same interface as `metrics_sql.facts_for`, different source. Nothing in this
file defines a metric: it names members of the Cube model compiled from
`semantic/brightwater.ossie.yaml` and turns the answers into `Facts`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from decimal import Decimal
from typing import Any

import httpx

from .policy import Facts, RelatedFacility


def _jwt(secret: str, claims: dict[str, Any]) -> str:
    """A minimal HS256 token. Cube ignores it in dev mode and requires it from Ch 8."""
    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(sig)}"


class Cube:
    def __init__(self, url: str | None = None, secret: str | None = None):
        self.url = (url or os.getenv("BRIGHTWATER_CUBE_URL", "http://localhost:4000")).rstrip("/")
        self.secret = secret or os.getenv("BRIGHTWATER_CUBE_SECRET", "brightwater-dev-secret")

    def load(self, query: dict[str, Any], timeout: float = 60.0) -> list[dict[str, Any]]:
        headers = {"Authorization": _jwt(self.secret, {"iat": int(time.time()), "sub": "brightwater-cli"})}
        deadline = time.monotonic() + timeout
        with httpx.Client(timeout=30) as http:
            while True:
                r = http.get(f"{self.url}/cubejs-api/v1/load", params={"query": json.dumps(query)}, headers=headers)
                body = r.json()
                if body.get("error") == "Continue wait" and time.monotonic() < deadline:
                    time.sleep(0.5)
                    continue
                if r.status_code != 200 or "error" in body:
                    raise RuntimeError(f"Cube error {r.status_code}: {body.get('error', body)}")
                return body["data"]


def _one(rows: list[dict[str, Any]], app_id: str) -> dict[str, Any]:
    if len(rows) != 1:
        raise KeyError(f"expected one row for {app_id}, got {len(rows)}")
    return rows[0]


def _bool(v: Any) -> bool:
    return v is True or (isinstance(v, str) and v.lower() == "true")


def facts_for(application_id: str, cube: Cube | None = None) -> Facts:
    cube = cube or Cube()
    where = lambda c: [{"member": f"{c}.application_id", "operator": "equals", "values": [application_id]}]  # noqa: E731

    p = _one(cube.load({"dimensions": ["application_profile.kyc_current",
                                       "application_profile.days_since_last_adverse_event",
                                       "application_profile.tenure_years"],
                        "filters": where("application_profile")}), application_id)
    dscr = _one(cube.load({"measures": ["application_month.dscr"], "filters": where("application_month")}),
                application_id)["application_month.dscr"]
    exposure = _one(cube.load({"measures": ["group_member.group_exposure"], "filters": where("group_member")}),
                    application_id)["group_member.group_exposure"]
    related = cube.load({"dimensions": ["related_facility.facility_id", "related_facility.business_id",
                                        "related_facility.owner_stake_pct", "related_facility.days_past_due"],
                         "filters": where("related_facility"),
                         "order": {"related_facility.facility_id": "asc"}})

    days_adverse = p["application_profile.days_since_last_adverse_event"]
    return Facts(
        application_id=application_id,
        kyc_current=_bool(p["application_profile.kyc_current"]),
        days_since_last_adverse_event=None if days_adverse is None else int(days_adverse),
        dscr=Decimal(str(dscr)).quantize(Decimal("0.0001")),
        group_exposure=Decimal(str(exposure)).quantize(Decimal("0.01")),
        related_facilities=tuple(
            RelatedFacility(r["related_facility.facility_id"], r["related_facility.business_id"],
                            Decimal(str(r["related_facility.owner_stake_pct"])).quantize(Decimal("0.01")),
                            int(r["related_facility.days_past_due"]))
            for r in related),
        tenure_years=Decimal(str(p["application_profile.tenure_years"])).quantize(Decimal("0.01")),
    )
