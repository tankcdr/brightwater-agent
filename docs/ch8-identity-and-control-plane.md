# Chapter 8. Identity, entitlements and the control plane

**Tag:** `ch8` · **Services:** as Chapter 4, plus Keycloak, OpenFGA, OPA and agentgateway (compose profile `control`) · **Proof:** a banker's run on an application they cover is filed; the same banker's run on one they do not cover fails before a single figure is read; a banker cannot publish a procedure and a procedure author can; every check is a row in `audit.authz`.

Until now the agent has acted for nobody in particular. It could read any application, and anyone who could reach the AIP server could publish a procedure that every client would then execute. A bank cannot run that. This chapter puts a name on every request, decides what that name may do in three separate places, and makes the database refuse what the layers above should already have refused.

## Four layers, and what each one stops

| Layer | Question it answers | Where it lives | What it stops |
|---|---|---|---|
| Identity | Who is asking? | Keycloak, realm `brightwater` | Anonymous requests |
| Entitlement | May this person assess this application? | OpenFGA, `policy/openfga/model.fga` | A banker assessing a customer they do not cover |
| Policy | May someone with these roles do this? | OPA, `policy/opa/brightwater.rego` | A banker publishing a procedure; a tool call with no entitlement |
| Row-level security | Which rows may this connection see? | Postgres, `schema/postgres/002_rls.sql` and `900_enable_rls.sql` | A bug in any layer above reading another banker's rows |

Each layer is small and does one job. Keeping them separate is the point: relationships (who covers whom) change every day and come from the systems of record, role rules change rarely and are reviewed like code, and the database is the one place every read must pass through.

### 1. Identity: Keycloak

`docker/keycloak/brightwater-realm.json` is imported when Keycloak starts. It defines two realm roles and five users, all with the password `brightwater`:

| User | Name | Role |
|---|---|---|
| `dokafor` | Delphine Okafor | `banker` |
| `mvelde` | Marcus Velde | `banker` |
| `ialbright` | Inez Albright | `banker` |
| `trenn` | Tobias Renn | `banker` |
| `pauthor` | Pat Author | `procedure_author` |

The four bankers are the same people as `relationship.banker` in Chapter 0, matched on the part of their email before the `@`. `brightwater auth login <user>` uses the public client `brightwater-cli` (password grant) and saves the token to `~/.brightwater/token.json` with mode 0600. The client has an audience mapper that adds `brightwater-gateway` to the access token, which is the audience agentgateway requires. `brightwater auth whoami` prints the username, roles and expiry that `identity.current_user()` reads from the token.

With enforcement on, `identity.claims()` verifies the token before anything reads it: the signature against the realm's published keys (`/realms/brightwater/protocol/openid-connect/certs`, fetched with PyJWT, MIT), the expiry, and the issuer. An edited, expired or foreign token identifies nobody, so every check below sees no user and refuses. agentgateway verifies the same token again in front of AIP, and the database role is what holds if the client process itself is wrong. `tests/test_ch8_rls.py::test_enforced_claims_are_verified` signs tokens with a realm key and a stranger's key and checks that only the first is accepted.

### 2. Entitlement: OpenFGA

The model is three types and one question:

```text
type user
type business
  relations
    define coverer: [user]
    define can_assess: coverer
type application
  relations
    define applicant: [business]
    define can_assess: can_assess from applicant
```

A user may assess an application if they cover the business that applied. `brightwater authz sync` creates a store, writes `policy/openfga/model.json` (the same model in JSON), and writes one tuple per row of `relationship.coverage` and one per row of `credit.application`, in batches of 100 because that is OpenFGA's limit per write. The store and model ids are saved to `~/.brightwater/openfga.json`. The entitlements are therefore derived from the systems of record, not maintained by hand: when coverage changes, you sync again.

On the default fixture, `dokafor` (BKR-01) covers Larkspur (`BUS-H01`, application `APP-H1`) and `BUS-H03`, while Northpine (`BUS-H04`, `APP-H4`) is covered by `mvelde`. `brightwater authz check dokafor APP-H4` asks OpenFGA directly and prints the answer.

Ownership is deliberately not modelled. Chapter 0 left `registry.ownership` readable across coverage, because the group exposure and related delinquency rules need to see an owner's stakes in businesses someone else covers. The model keeps that choice: there is no relation that hides a stake.

### 3. Policy: OPA

`policy/opa/brightwater.rego` (package `brightwater`) makes two decisions, both default false:

- **`allow`**, asked by agentgateway before a request reaches the AIP server. Any `banker` or `procedure_author` may read the catalog and run procedures. Catalog writes (`POST`, `PUT` or `DELETE` under `/catalog`, which covers publish, pin and retire) and governance writes (`PUT` or `DELETE` under `/governance`) need `procedure_author`. Publishing is shipping code: every client that runs a procedure executes its scripts, so a banker must not be able to change them.
- **`tool_allow`**, asked by the tool wrapper before a tool call. The caller must have the `banker` role. `get_policy` reads no customer data and is allowed for any banker. Every other tool needs `input.entitled == true`, which is OpenFGA's answer, passed in by the wrapper.

The wrapper is `guarded()` in `src/brightwater/tools.py`. With enforcement on, `identity.tool_allowed()` asks OpenFGA `can_assess`, sends the tool name, roles, entitlement, user and application id to OPA at `/v1/data/brightwater/tool_allow`, and if OPA says no, the tool returns `{"error": "denied by policy: ..."}` instead of data. `brightwater tool` and the free-form agent from Chapter 3 both use the guarded tools.

The compiled procedure's scripts call the same guarded tools, so `tool_allow` is consulted on every tool call there too, and a denial stops the run. Before any of that, the `gather` step in `src/brightwater/procedure_steps.py` calls `identity.require_assess()`, which asks OpenFGA and raises `PermissionError` if the signed-in user is not entitled. That happens before `get_application`, `get_figures` or any Cube query runs, so an unentitled run reads nothing. OPA also makes the `allow` decision at the gateway, for every request to the AIP server.

`policy/opa/brightwater_test.rego` holds seven tests: a banker can start a run, a banker cannot publish, an author can, a user with no role is refused, a data tool needs entitlement, a data tool with entitlement is allowed, and `get_policy` is public.

### 4. Row-level security: Postgres

Chapter 0 created the policies but left them inactive. Each one calls `relationship.covers(business_id)`, which checks `relationship.coverage` against the session setting `brightwater.banker_id`. They apply to the ledger (business, deposit accounts, monthly cash flow), credit (facilities, payments, applications), compliance (KYC, adverse events) and relationship notes. `registry.ownership` has no policy, for the reason above.

`brightwater authz rls-on` runs `schema/postgres/900_enable_rls.sql`. It creates the login role `brightwater_app` with `NOBYPASSRLS`, grants it read access to the data schemas and `SELECT, INSERT, UPDATE` on `agent.decision`, and enables row-level security on every table with a policy. `db.init_schema()` only applies files starting with `0`, so this step never runs by accident in earlier chapters.

With enforcement on, `db.connect(agent=True)` connects as `brightwater_app` and sets `brightwater.banker_id` to the signed-in banker's id, looked up from `relationship.banker`. If the user cannot be resolved, the banker id is set to `nobody`, which covers nothing, so the connection sees no rows at all. The tools' own SQL reads (`get_application`, `get_notes`) and filing go through this connection. Every connection also carries `application_name` `brightwater:<decision_id>`, which Chapter 9 uses to join pgaudit lines to a decision.

Be precise about what this layer covers. Cube and the memory graph read under their own service identities, and the owner role `brightwater` still bypasses RLS, because Cube reads across coverage on purpose (Northpine's banker needs Ridgeback's days past due, not Ridgeback's ledger). For those reads, the entitlement check before the run is the control. RLS guarantees that the agent's own Postgres connections cannot see another banker's customers, whatever the code above them does.

## agentgateway in front of the AIP server

`gateway/config.yaml` configures agentgateway on two ports. Port 3100 fronts the AIP server, and every request to it passes three policies in order:

1. **`jwtAuth`** verifies the Keycloak token: issuer `http://localhost:8180/realms/brightwater`, audience `brightwater-gateway`, keys fetched from Keycloak's JWKS endpoint.
2. **`extAuthz`** posts `{"input": {"method", "path", "user", "roles"}}` to OPA at `/v1/data/brightwater/allow` and keeps the result as `extauthz.decision`.
3. **`authorization`** requires `extauthz.decision == true`.

Only then is the request forwarded to `aip-server:8000`, with the caller's token replaced by the server's own shared token. The banker's token stops at the gateway. The access log adds the username and OPA's decision to every line, and is also set up to add `x-brightwater-agent` and `x-brightwater-decision` request headers. Port 3101 is configured as a logging proxy for the model API (`claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`).

Two limits of this local setup. The AIP server's own port 8000 is still published by `compose.yaml` for Chapters 4 to 7, so the gateway is only in front of AIP for clients configured to use port 3100; in a real deployment port 8000 would not be reachable. And in this build nothing yet sends the two `x-brightwater-*` headers or points the driver at port 3101, so those log fields will be empty.

## Off by default, closed when on

Every check in this chapter is behind `BRIGHTWATER_ENFORCE=on` (`identity.enforced()`). Without it, Chapters 0 to 7 run exactly as before, as the owner role, with no token. With it, the checks fail closed:

- No signed-in user: `tool_allowed` refuses and `require_assess` raises.
- OpenFGA or OPA unreachable or returning an error: the HTTP call raises, and the tool or step fails rather than proceeding.
- A user with no matching banker: the database session is bound to `nobody` and sees no rows.

## Every decision is logged

`audit.authz` (`schema/postgres/004_audit.sql`) has one row per check: the decision id (from `BRIGHTWATER_DECISION_ID`, which the procedure driver mints for each run), the engine (`openfga` or `opa`), the subject `user:<username>`, the action (the relation or tool name), the object, whether it was allowed, and details such as the time OpenFGA took and the roles OPA saw. `brightwater_app` may insert into it but not update it. Chapter 9 joins these rows into the audit packet, so a filed decision carries the entitlement checks made on its behalf.

With enforcement on, a check that cannot be written to `audit.authz` does not pass: the write error propagates and the step fails closed, so a filed decision cannot be missing its checks. Two caveats. The gateway's OPA decisions go to the agentgateway access log, not to `audit.authz`. And the rows in `audit.authz` are written by the brightwater process itself, so they record what the client says it checked: `brightwater_app` may insert into the table, and a compromised client could insert a row saying a check passed. They are useful evidence, not independent evidence. The independent record is each engine's own log (OPA's decision log, OpenFGA's, the gateway's access log); in production those are shipped to the audit store directly, and the client's copy is checked against them.

## Do it yourself

> How the chapters fit together, what carries over between them, and fixes for the common errors: [Working through the chapters](working-through-the-chapters.md).

Keycloak, OpenFGA, OPA and agentgateway were not available in the environment where this walkthrough was checked, so step 4, the signed-in runs and the gateway, is shown as commands only. Steps 1 to 3 need only Postgres, Cube and the AIP server, and they show the two properties that matter most: the database refuses on its own, and with enforcement on, nobody signed in means nothing is read.

**1. Branch from the chapter's tag and start the control plane.**

```bash
git switch -c my-ch8 ch8
docker compose --profile aip --profile control up -d --wait
source .venv/bin/activate
uv pip install -e ".[dev]"
```

The install picks up any dependencies this chapter adds. Every `brightwater` command runs inside the virtual environment: in a new terminal, run `source .venv/bin/activate` from the repo first, or put `uv run` in front of the command.

Then:

```bash
brightwater fixture generate
brightwater db init && brightwater db load && brightwater policy sync
aip config --server http://localhost:8000 --token brightwater-aip-local
aip publish skills/brightwater-loan-assessment
rm -f ~/.brightwater/token.json
```

These lines bring the fixture, the database, the AIP client and the published procedure to a known state, and sign you out, which step 3 assumes; they take a few seconds and are safe to rerun. `db init` now also applies `004_audit.sql`, which creates `audit.authz` (and `audit.packet`, used in Chapter 9). The pinned images are `quay.io/keycloak/keycloak:26.8.2`, `openfga/openfga:v1.22.1`, `openpolicyagent/opa:1.22.2` and `cr.agentgateway.dev/agentgateway:v1.7.0`. All four projects are Apache-2.0 licensed; agentgateway is hosted by the Agentic AI Foundation under the Linux Foundation.

**2. Switch on row-level security.**

```bash
brightwater authz rls-on
```

```text
row-level security enabled for brightwater_app
```

The policies have existed since Chapter 0; this creates the agent's login role, `brightwater_app`, without the right to bypass them, and enables them. Your own `brightwater` role still owns the tables and still sees everything, which is why the earlier chapters keep working.

**3. Turn enforcement on with nobody signed in.**

```bash
export BRIGHTWATER_ENFORCE=on
brightwater auth whoami
brightwater tool get_figures APP-H1
brightwater procedure run APP-H1 --model scripted
```

```text
{
  "username": null,
  "roles": [],
  "subject": null,
  "expires": null
}
{
  "error": "denied by policy: no signed-in user (brightwater auth login)"
}
...
aip.sdk.RunError: Script scripts/steps.py exited with 1:
...
PermissionError: no signed-in user: run `brightwater auth login`
```

The tool refuses before reading anything, and the procedure stops in `gather`, before `get_application`, `get_figures` or any Cube query. Nothing asked OpenFGA or OPA anything, because there was nobody to ask about. This is what "closed when on" means: the absence of an identity is a refusal, not a default identity. Run `unset BRIGHTWATER_ENFORCE` before going on.

**4. Sign in and run the full control plane (needs Keycloak, OpenFGA, OPA and agentgateway).**

```bash
brightwater authz sync
brightwater auth login dokafor --password brightwater
aip config --server http://localhost:3100 --token "$(python -c 'import json,pathlib;print(json.loads((pathlib.Path.home()/".brightwater/token.json").read_text())["access_token"])')"
export BRIGHTWATER_ENFORCE=on
brightwater procedure run APP-H1 --model scripted
brightwater procedure run APP-H4 --model scripted
aip publish skills/brightwater-loan-assessment
brightwater auth login pauthor --password brightwater
aip config --server http://localhost:3100 --token "$(python -c 'import json,pathlib;print(json.loads((pathlib.Path.home()/".brightwater/token.json").read_text())["access_token"])')"
aip publish skills/brightwater-loan-assessment
docker compose logs agentgateway --tail 50
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -c \
  "select engine, subject, action, object, allowed from audit.authz order by id desc limit 10"
```

Read the middle lines in pairs. `dokafor` covers Larkspur, so the APP-H1 run is filed; Northpine is `mvelde`'s, so the APP-H4 run fails closed in `gather`; `dokafor` is a banker, so her publish is rejected at the gateway. Then `pauthor` signs in, the client is pointed at the gateway with her token, and her publish, as a procedure author, is accepted. Two of these lines are meant to fail, so this block is the exception to stopping at the first error. Keycloak has no health check in `compose.yaml`, so `--wait` in step 1 does not wait for it to finish starting; if `auth login` fails with a connection error, wait a minute and run it again.

Put it back before going on, so that the exercises and Chapter 9 start signed out, with enforcement off and the client talking to the AIP server directly:

```bash
unset BRIGHTWATER_ENFORCE
aip config --server http://localhost:8000 --token brightwater-aip-local
rm ~/.brightwater/token.json
```

The outputs it produces are under [The proof](#the-proof), and RUNBOOK.md, Ch 8 saves each one under `results/live/`. OpenFGA runs with its default in-memory store, so run `brightwater authz sync` again after restarting it.

**5. Run the proof that needs only Postgres.**

```bash
pytest -q tests/test_ch8_rls.py
```

```text
5 passed
```

Four tests check row-level security as described in the proof below, and the fifth, `test_enforced_claims_are_verified`, checks that only a token signed with the realm's key is accepted.

## The proof

**Verified here** (`tests/test_ch8_rls.py`, run in this build environment against Postgres, 5 passed): `brightwater_app` bound to BKR-01 sees `APP-H1` and not `APP-H4`, and none of Northpine's monthly cash flow; the same connection still sees both ownership rows of `BUS-H90`; a connection with no banker sees no applications at all; the agent's role cannot update `audit.packet`; and with enforcement on, a token signed by any key other than the realm's, or an expired one, identifies nobody.

The OPA tests in `policy/opa/brightwater_test.rego` exist and CI runs them (`docker run --rm -v "$PWD/policy/opa:/policy" openpolicyagent/opa:1.22.2 test /policy -v`): `PASS: 7/7`.

**Verified live** (RUNBOOK.md, Ch 8, on 2026-11-10): Keycloak, OpenFGA, OPA and agentgateway running, with enforcement on.

```text
$ brightwater procedure run APP-H1 --model scripted          # signed in as dokafor
APP-H1 run 1: approve  filed           aip 2b2c4f98bd4b43b7  steps gather>apply-policy>assess>draft-rationale>file>needs-person

$ brightwater procedure run APP-H4 --model scripted
PermissionError: dokafor is not entitled to assess APP-H4

$ aip publish skills/brightwater-loan-assessment              # as dokafor
aip: http_error: HTTP 403: authorization failed

$ aip publish skills/brightwater-loan-assessment              # as pauthor
brightwater-loan-assessment@d005aa1a7fce1d5f
```

The APP-H1 decision row carries `user_subject` `user:dokafor`, and `audit.authz` has the refusal as `openfga | user:dokafor | can_assess | application:APP-H4 | false`. The two publishes in the gateway's log:

```text
http.method=POST http.path=/catalog http.status=403 user="dokafor" opa_decision=false
http.method=POST http.path=/catalog http.status=201 user="pauthor" opa_decision=true
```

What to look for: the `APP-H1` run is filed with `user_subject` `user:dokafor` on the decision row; the `APP-H4` run stops in `gather` with `dokafor is not entitled to assess APP-H4`, and `audit.authz` has an `openfga` row with `allowed` false; the banker's publish is refused by the gateway and never reaches the AIP server; the author's publish is accepted (`201`), and creates a revision if the skill changed since the last publish (here it had not, so AIP returned revision 2); and each gateway log line names the user and OPA's decision.

## Try it

**1. Be a banker who does not cover the application.** The full version of this exercise is step 4's second run: sign in as `dokafor` and assess `APP-H4`, which `mvelde` covers. It needs the control plane, and it was not run here. The database layer underneath can be tried on its own: connect as the agent's role and bind the session to Delphine Okafor's banker id, `BKR-01`, the way `db.connect(agent=True)` does.

```bash
docker compose exec -T postgres psql postgresql://brightwater_app:brightwater_app@localhost/brightwater \
  -c "set brightwater.banker_id = 'BKR-01'" \
  -c "select application_id, business_id from credit.application where application_id in ('APP-H1','APP-H4')" \
  -c "select count(*) as northpine_months from ledger.monthly_cash_flow cf join ledger.deposit_account d using (account_id) where d.business_id = 'BUS-H04'" \
  -c "select person_id, business_id, pct from registry.ownership where business_id = 'BUS-H90'"
```

Which applications will she see? Will she see Northpine's cash flow? Ridgeback's owners? Then try `BKR-02` (Marcus Velde), and `nobody`.

<details><summary>What happens, and why it matters</summary>

```text
 application_id | business_id
----------------+-------------
 APP-H1         | BUS-H01

 northpine_months
------------------
                0

 person_id | business_id |  pct
-----------+-------------+-------
 PER-H05   | BUS-H90     | 35.00
 PER-H07   | BUS-H90     | 65.00
```

Bound to `BKR-01`, the connection sees Larkspur's application and not Northpine's, and none of Northpine's monthly cash flow, but it still sees who owns Ridgeback. That is the split from Chapter 0: ledgers follow coverage, ownership does not, because P4 and P5 need to see stakes in businesses other bankers cover. With `BKR-02` the same query returns `APP-H4` and not `APP-H1`. With `nobody`, or with no banker set at all, `credit.application` returns no rows.

None of the layers above was involved. If OpenFGA, OPA or the tool wrapper had a bug that let a request through, this is what the agent's own connection would still see.
</details>

**2. Forge a token.** Make a token that claims to be Delphine Okafor, signed with a key that is not the realm's, and hand it to the CLI:

```bash
export BRIGHTWATER_TOKEN=$(python -c "import jwt,time; print(jwt.encode({'preferred_username':'dokafor','realm_access':{'roles':['banker']},'exp':int(time.time())+3600,'iss':'http://localhost:8180/realms/brightwater'}, 'not-the-realm-key-but-long-enough-for-hs256', algorithm='HS256'))")
brightwater auth whoami
BRIGHTWATER_ENFORCE=on brightwater procedure run APP-H1 --model scripted
```

Who does `whoami` say you are? Does the run get filed?

<details><summary>What happens</summary>

```text
{
  "username": "dokafor",
  "roles": [
    "banker"
  ],
  "subject": "user:dokafor",
  ...
}
...
jwt.exceptions.PyJWKClientConnectionError: Fail to fetch data from the url, err: "<urlopen error [Errno 111] Connection refused>"
```

With enforcement off, `whoami` only reads the claims, so the forgery reads as `dokafor`. That is fine for a display command and is exactly why nothing in Chapters 0 to 7 trusts it. With enforcement on, `identity.claims()` has to verify the signature against the realm's published keys before anything reads the token. Here Keycloak is not running (on a Mac the error says `[Errno 61]`, macOS's number for the same refusal), so the keys cannot be fetched, and the driver fails when it asks who the user is, before it has started a run; nothing is read and nothing is filed. With Keycloak running, the keys are fetched, the signature does not match, the token identifies nobody, and the run fails as in step 3. `test_enforced_claims_are_verified` checks that second case.

Undo it: `unset BRIGHTWATER_TOKEN`.
</details>

## What to carry forward

Every decision now has a person behind it, and every check made for that person is a row with the decision's id on it. The pieces of evidence for one decision are spread across Postgres, the memory graph, the AIP server, the trace and the logs. Chapter 9 gathers them into one sealed packet per decision.

**Previous:** [Chapter 7](ch7-private-inference.md) · **Next:** Chapter 9. The audit packet
