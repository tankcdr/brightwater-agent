"""Chapter 8, the parts that need only Postgres: row-level security for the
agent's role, the ownership exception, and append-only audit tables.

OpenFGA, OPA, Keycloak and agentgateway run in compose; their proofs are in
docs/ch8-identity-and-control-plane.md and RUNBOOK.md.
"""

from __future__ import annotations

import pg8000.native
import pytest

from brightwater import db

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def rls(loaded_db):
    for f in ("003_decisions.sql", "004_audit.sql", "900_enable_rls.sql"):
        loaded_db.run((db.SCHEMA_DIR / f).read_text())


def app_conn(banker: str) -> pg8000.native.Connection:
    c = pg8000.native.Connection(user="brightwater_app", password="brightwater_app", host="localhost",
                                 database="brightwater")
    c.run("SELECT set_config('brightwater.banker_id', :b, false)", b=banker)
    return c


def test_banker_sees_only_covered_applications(rls):
    c = app_conn("BKR-01")                       # covers Larkspur (APP-H1), not Northpine (APP-H4)
    assert c.run("SELECT count(*) FROM credit.application WHERE application_id = 'APP-H1'")[0][0] == 1
    assert c.run("SELECT count(*) FROM credit.application WHERE application_id = 'APP-H4'")[0][0] == 0
    assert c.run("SELECT count(*) FROM ledger.monthly_cash_flow cf JOIN ledger.deposit_account d USING (account_id) "
                 "WHERE d.business_id = 'BUS-H04'")[0][0] == 0
    c.close()


def test_ownership_stays_visible_across_coverage(rls):
    c = app_conn("BKR-01")
    assert c.run("SELECT count(*) FROM registry.ownership WHERE business_id = 'BUS-H90'")[0][0] == 2
    c.close()


def test_no_banker_sees_nothing(rls):
    c = app_conn("")
    assert c.run("SELECT count(*) FROM credit.application")[0][0] == 0
    c.close()


def test_audit_packets_are_append_only_for_the_agent(rls):
    c = app_conn("BKR-01")
    with pytest.raises(pg8000.native.DatabaseError):
        c.run("UPDATE audit.packet SET packet = '{}'::jsonb")
    c.close()


def test_enforced_claims_are_verified(monkeypatch):
    """With enforcement on, only a token signed by the realm's key identifies anyone."""
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from brightwater import identity

    realm_key, other_key = (rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2))

    class Keys:
        def __init__(self, url):
            pass

        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": realm_key.public_key()})()

    monkeypatch.setattr(jwt, "PyJWKClient", Keys)
    monkeypatch.setattr(identity, "log", lambda *a, **k: None)
    monkeypatch.setenv("BRIGHTWATER_ENFORCE", "on")
    body = {"preferred_username": "dokafor", "iss": identity._issuer(), "exp": int(time.time()) + 300}

    good = jwt.encode(body, realm_key, algorithm="RS256")
    forged = jwt.encode(body, other_key, algorithm="RS256")
    expired = jwt.encode({**body, "exp": int(time.time()) - 5}, realm_key, algorithm="RS256")
    assert identity.claims(good)["preferred_username"] == "dokafor"
    assert identity.claims(forged) == {} and identity.claims(expired) == {}
