"""Seeded, deterministic fixture generator for the five systems of record.

    brightwater fixture generate --seed 7 --n 1200 --out fixture/out

produces one CSV per table plus a manifest of SHA-256 hashes. The same seed
and the same `fixture/handbuilt.yaml` always produce byte-identical files;
`tests/test_fixture.py` checks that.

Two populations come out of one run:

* the six hand-built applicants in `fixture/handbuilt.yaml`, each built to hit
  one decision path, and
* `n` generated applicants (plus about n/4 affiliate businesses that share
  owners with them) spread across every band of the policy. Ch 6 trains and
  evaluates the decision model on these.

Every name is assembled from the word lists below. None of it describes a real
business or person.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

CENT = Decimal("0.01")
DEFAULT_HANDBUILT = Path(__file__).resolve().parents[2] / "fixture" / "handbuilt.yaml"

# Column order for every table. The CSV header and the COPY column list both
# come from here, so the loader cannot drift from the generator.
COLUMNS: dict[str, list[str]] = {
    "ledger.business": ["business_id", "legal_name", "naics", "state", "established", "customer_since"],
    "ledger.deposit_account": ["account_id", "business_id", "opened"],
    "ledger.monthly_cash_flow": ["account_id", "month", "operating_inflow", "nonoperating_inflow", "outflow"],
    "credit.facility": ["facility_id", "business_id", "kind", "opened", "original_amount",
                        "outstanding_balance", "monthly_payment"],
    "credit.payment": ["facility_id", "due_date", "amount_due", "paid_date", "amount_paid"],
    "credit.application": ["application_id", "business_id", "requested_amount", "term_months",
                           "annual_rate", "submitted_on", "purpose", "status"],
    "compliance.kyc": ["business_id", "status", "last_reviewed", "next_review_due"],
    "compliance.adverse_event": ["event_id", "business_id", "occurred_on", "kind", "summary"],
    "registry.person": ["person_id", "full_name", "date_of_birth"],
    "registry.ownership": ["person_id", "business_id", "pct"],
    "relationship.banker": ["banker_id", "full_name", "email"],
    "relationship.coverage": ["banker_id", "business_id"],
    "relationship.note": ["note_id", "business_id", "banker_id", "written_on", "body"],
}

# ------------------------------------------------------------------ word lists
_PLACES = ["Alder", "Bramble", "Cedar Hollow", "Driftwood", "Elmstead", "Fernbrook", "Glenmoor", "Hatteras",
           "Ironweed", "Juniper", "Kestrel", "Lantern", "Millrace", "Nettle", "Oxbow", "Pelican", "Quarry Hill",
           "Rushmere", "Sable", "Thistle", "Underhill", "Vesper", "Willowmere", "Yarrow", "Bluestem", "Copperhead",
           "Dunmore", "Eastgate", "Foxglove", "Granite Shoals", "Heron Point", "Indigo", "Kettle Creek", "Lowcountry",
           "Marsh Hen", "Northfork", "Old Mill", "Pinehurst Row", "Red Clay", "Sandhill", "Tanager", "Wicklow"]
_TRADES = [("Bakery", "311811"), ("Machine Works", "332710"), ("Freight", "484121"), ("Dental Lab", "339116"),
           ("Roofing", "238160"), ("Brewing", "312120"), ("Print Shop", "323111"), ("Landscaping", "561730"),
           ("Veterinary Clinic", "541940"), ("Auto Body", "811121"), ("Cabinetry", "337110"),
           ("HVAC Services", "238220"), ("Coffee Roasters", "311920"), ("Physical Therapy", "621340"),
           ("Welding", "811310"), ("Seafood Market", "445250"), ("Hardware", "444140"), ("Daycare", "624410"),
           ("Signworks", "339950"), ("Tile Co", "238340"), ("Outfitters", "459110"), ("Storage", "493110"),
           ("Ceramics", "327110"), ("Marine Supply", "423860"), ("Bindery", "323120"), ("Electric", "238210")]
_SUFFIXES = ["LLC", "Inc", "Co", "LLC", "LLC", "Corp"]
_STATES = ["NC", "SC", "VA", "GA", "TN"]
_FIRST = ["Ada", "Basil", "Corinne", "Dashiell", "Elowen", "Fitz", "Greer", "Hollis", "Imogen", "Jasper", "Keziah",
          "Lyle", "Marisol", "Nils", "Ottilie", "Percival", "Quinlan", "Rosalind", "Silas", "Thea", "Ulric",
          "Verity", "Wyatt", "Xiomara", "Yusuf", "Zelda", "Anselm", "Beatrix", "Cormac", "Delphina", "Emrys",
          "Florian", "Gwendolyn", "Hester", "Ignatius", "Juno", "Kit", "Leopold", "Mirela", "Nico"]
_LAST = ["Abernathy", "Blackwood", "Carrow", "Dunleavy", "Eckhart", "Fairweather", "Galloway", "Hargrove",
         "Ingersoll", "Jessup", "Kilbride", "Lockhart", "Merriweather", "Northcutt", "Oakes", "Pembroke",
         "Quillen", "Ravenscroft", "Sutherland", "Thornbury", "Upchurch", "Vantongeren", "Whitlock", "Yardley",
         "Zabinski", "Ashgrove", "Bellweather", "Coldwell", "Darrow", "Esterbrook", "Fenwick", "Greenhalgh",
         "Holloway", "Ivers", "Kingsley", "Larrabee", "Marchetti", "Nightingale", "Okonkwo", "Prewitt"]
_PURPOSES = ["equipment", "working capital", "vehicle", "real estate improvement", "inventory",
             "refinance existing debt", "expansion", "technology upgrade"]
_ADVERSE = [("litigation", "Civil judgment entered against the business."),
            ("tax_lien", "State tax lien recorded."),
            ("fraud_alert", "Deposit account flagged for returned-item fraud review."),
            ("sanctions_hit", "Name screening match pending disposition.")]
_NOTE_TEMPLATES = [
    "Met with the owner on site. Operations look orderly and the books are current.",
    "Owner asked about extending the line before the busy season.",
    "Quarterly check-in. Revenue is tracking last year; margins slightly thinner on input costs.",
    "Referral from an existing customer. Wants to consolidate banking with us.",
    "Owner mentioned a partner's other business is going through a rough patch.",
    "Lost a large customer in the spring; replaced about half of that volume so far.",
]


def money(x: float | Decimal) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def amortized_payment(principal: Decimal, annual_rate: Decimal, term_months: int) -> Decimal:
    """Level monthly payment. Matches the SQL used by the metrics layer."""
    r = float(annual_rate) / 12.0
    p = float(principal)
    return Decimal(str(p * r / (1.0 - (1.0 + r) ** (-term_months))))


def dscr_window(submitted_on: date) -> list[date]:
    """The 12 full calendar months before the month of application."""
    first = add_months(month_start(submitted_on), -12)
    return [add_months(first, i) for i in range(12)]


# ------------------------------------------------------------------ output
@dataclass
class Dataset:
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {t: [] for t in COLUMNS})
    handbuilt_ids: list[str] = field(default_factory=list)
    held_out_ids: list[str] = field(default_factory=list)

    def add(self, table: str, **row: Any) -> None:
        missing = set(COLUMNS[table]) - set(row)
        for col in missing:
            row[col] = None
        self.tables[table].append(row)


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)


def write_csv(ds: Dataset, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, str] = {}
    for table, cols in COLUMNS.items():
        path = out_dir / f"{table}.csv"
        rows = sorted(ds.tables[table], key=lambda r: tuple(_fmt(r[c]) for c in cols))
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(cols)
            for r in rows:
                w.writerow([_fmt(r[c]) for c in cols])
        manifest[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = {"files": manifest, "handbuilt": ds.handbuilt_ids, "held_out": ds.held_out_ids,
            "rows": {t: len(r) for t, r in ds.tables.items()}}
    (out_dir / "manifest.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return manifest


# ------------------------------------------------------------------ builders
class _Builder:
    def __init__(self, seed: int, as_of: date):
        self.rng = random.Random(seed)
        self.as_of = as_of
        self.ds = Dataset()
        self.ledger_months = [add_months(month_start(as_of), -i) for i in range(14, -1, -1)]  # 15 months
        self._names: set[str] = set()
        self._people: set[str] = set()
        self._fac_seq: dict[str, int] = {}
        self._note_seq = 0
        self._event_seq = 0

    # -- names
    def business_name(self, trade: str) -> str:
        for _ in range(100):
            name = f"{self.rng.choice(_PLACES)} {trade} {self.rng.choice(_SUFFIXES)}"
            if name not in self._names:
                self._names.add(name)
                return name
        raise RuntimeError("name space exhausted")

    def person_name(self) -> str:
        for _ in range(1000):
            name = f"{self.rng.choice(_FIRST)} {self.rng.choice(_LAST)}"
            if name not in self._people:
                self._people.add(name)
                return name
        # The lists give 1,600 unique pairs; past that, add a middle initial.
        while True:
            name = f"{self.rng.choice(_FIRST)} {chr(65 + self.rng.randrange(26))}. {self.rng.choice(_LAST)}"
            if name not in self._people:
                self._people.add(name)
                return name

    # -- rows
    def business(self, business_id: str, legal_name: str, naics: str, state: str,
                 established: date, customer_since: date, banker_id: str) -> None:
        self.ds.add("ledger.business", business_id=business_id, legal_name=legal_name, naics=naics,
                    state=state, established=established, customer_since=customer_since)
        self.ds.add("relationship.coverage", banker_id=banker_id, business_id=business_id)

    def facility(self, business_id: str, kind: str, original_amount: float, outstanding_balance: float,
                 monthly_payment: float, opened: date, missed_payments: int = 0) -> str:
        n = self._fac_seq.get(business_id, 0) + 1
        self._fac_seq[business_id] = n
        facility_id = f"FAC-{business_id.removeprefix('BUS-')}-{n}"
        pay = money(monthly_payment)
        self.ds.add("credit.facility", facility_id=facility_id, business_id=business_id, kind=kind,
                    opened=opened, original_amount=money(original_amount),
                    outstanding_balance=money(outstanding_balance), monthly_payment=pay)
        dues = [add_months(month_start(self.as_of), -i) for i in range(11, -1, -1)]  # 12 due dates
        dues = [d for d in dues if d > opened]
        for i, due in enumerate(dues):
            missed = i >= len(dues) - missed_payments
            if missed:
                self.ds.add("credit.payment", facility_id=facility_id, due_date=due, amount_due=pay)
            else:
                paid = due + timedelta(days=self.rng.randrange(0, 6))
                self.ds.add("credit.payment", facility_id=facility_id, due_date=due, amount_due=pay,
                            paid_date=paid, amount_paid=pay)
        return facility_id

    def cash_flows(self, business_id: str, opened: date, window: list[date] | None,
                   window_net_total: Decimal | None, injections: dict[date, Decimal],
                   n_accounts: int) -> None:
        accounts = [f"ACC-{business_id.removeprefix('BUS-')}-{i + 1}" for i in range(n_accounts)]
        for a in accounts:
            self.ds.add("ledger.deposit_account", account_id=a, business_id=business_id, opened=opened)
        margin = self.rng.uniform(0.08, 0.20)
        # Net cash flow per month. Inside the DSCR window the 12 values sum to
        # exactly window_net_total; outside it they are on the same scale.
        base = (window_net_total / 12) if window_net_total is not None else money(self.rng.uniform(4000, 40000))
        nets: dict[date, Decimal] = {}
        if window:
            weights = [1 + self.rng.uniform(-0.15, 0.15) for _ in window]
            s = sum(weights)
            vals = [money(window_net_total * Decimal(str(w / s))) for w in weights]
            vals[-1] += window_net_total - sum(vals)
            nets.update(zip(window, vals))
        for m in self.ledger_months:
            if m not in nets:
                nets[m] = money(base * Decimal(str(1 + self.rng.uniform(-0.15, 0.15))))
        split = Decimal("0.9") if n_accounts > 1 else Decimal("1")
        for m in self.ledger_months:
            net = nets[m]
            inflow = money(max(net, Decimal(0)) / Decimal(str(margin)) + Decimal(1000))
            outflow = inflow - net
            inj = injections.get(m, Decimal(0))
            parts = [(inflow, inj, outflow)]
            if n_accounts > 1:
                i1, j1, o1 = money(inflow * split), money(inj * split), money(outflow * split)
                parts = [(i1, j1, o1), (inflow - i1, inj - j1, outflow - o1)]
            for acc, (i, j, o) in zip(accounts, parts):
                self.ds.add("ledger.monthly_cash_flow", account_id=acc, month=m, operating_inflow=i,
                            nonoperating_inflow=j, outflow=o)

    def note(self, business_id: str, banker_id: str, written_on: date, body: str) -> None:
        self._note_seq += 1
        self.ds.add("relationship.note", note_id=f"NOTE-{self._note_seq:05d}", business_id=business_id,
                    banker_id=banker_id, written_on=written_on, body=body)

    def adverse(self, business_id: str, occurred_on: date) -> None:
        self._event_seq += 1
        kind, summary = self.rng.choice(_ADVERSE)
        self.ds.add("compliance.adverse_event", event_id=f"ADV-{self._event_seq:05d}", business_id=business_id,
                    occurred_on=occurred_on, kind=kind, summary=summary)

    def applicant(self, *, application_id: str, business_id: str, requested_amount: Decimal,
                  term_months: int, annual_rate: Decimal, submitted_on: date, purpose: str,
                  existing_payments: Decimal, target_dscr: Decimal, injections: dict[date, Decimal],
                  opened: date, n_accounts: int) -> None:
        self.ds.add("credit.application", application_id=application_id, business_id=business_id,
                    requested_amount=money(requested_amount), term_months=term_months,
                    annual_rate=annual_rate, submitted_on=submitted_on, purpose=purpose, status="open")
        monthly_debt = existing_payments + amortized_payment(requested_amount, annual_rate, term_months)
        window = dscr_window(submitted_on)
        total = money(target_dscr * monthly_debt * 12)
        self.cash_flows(business_id, opened, window, total, injections, n_accounts)


# ------------------------------------------------------------------ hand-built
def _load_handbuilt(b: _Builder, spec: dict[str, Any]) -> None:
    for bk in spec["bankers"]:
        b.ds.add("relationship.banker", **bk)
    for p in spec["people"]:
        b._people.add(p["full_name"])
        b.ds.add("registry.person", **p)

    def owners(business_id: str, owner_list: list[dict[str, Any]]) -> None:
        for o in owner_list:
            b.ds.add("registry.ownership", person_id=o["person_id"], business_id=business_id,
                     pct=Decimal(str(o["pct"])).quantize(CENT))

    for a in spec["affiliates"]:
        b._names.add(a["legal_name"])
        b.business(a["business_id"], a["legal_name"], a["naics"], a["state"], a["established"],
                   a["customer_since"], a["banker_id"])
        owners(a["business_id"], a["owners"])
        for f in a.get("facilities", []):
            b.facility(a["business_id"], **f)
        b.ds.add("compliance.kyc", business_id=a["business_id"], status="current",
                 last_reviewed=date(2026, 1, 15), next_review_due=date(2027, 1, 15))
        b.cash_flows(a["business_id"], a["customer_since"], None, None, {}, 1)

    for a in spec["applicants"]:
        b._names.add(a["legal_name"])
        b.ds.handbuilt_ids.append(a["application_id"])
        if a.get("held_out"):
            b.ds.held_out_ids.append(a["application_id"])
        bid = a["business_id"]
        b.business(bid, a["legal_name"], a["naics"], a["state"], a["established"], a["customer_since"],
                   a["banker_id"])
        owners(bid, a["owners"])
        existing = Decimal(0)
        for f in a.get("facilities", []):
            b.facility(bid, **f)
            existing += Decimal(str(f["monthly_payment"]))
        b.ds.add("compliance.kyc", business_id=bid, **a["kyc"])
        for n in a.get("notes", []):
            b.note(bid, n["banker_id"], n["written_on"], n["body"])
        app = a["application"]
        b.applicant(application_id=a["application_id"], business_id=bid,
                    requested_amount=Decimal(str(app["requested_amount"])), term_months=app["term_months"],
                    annual_rate=Decimal(str(app["annual_rate"])), submitted_on=app["submitted_on"],
                    purpose=app["purpose"], existing_payments=existing,
                    target_dscr=Decimal(str(a["target_dscr"])),
                    injections={i["month"]: money(i["amount"]) for i in a.get("injections", [])},
                    opened=a["customer_since"], n_accounts=1)


# ------------------------------------------------------------------ generated
def _generate(b: _Builder, n: int) -> None:
    rng = b.rng
    bankers = [r["banker_id"] for r in b.ds.tables["relationship.banker"]]
    people: list[str] = []
    person_seq = 0

    def new_person() -> str:
        nonlocal person_seq
        person_seq += 1
        pid = f"PER-{person_seq:05d}"
        dob = date(rng.randrange(1950, 1996), rng.randrange(1, 13), rng.randrange(1, 29))
        b.ds.add("registry.person", person_id=pid, full_name=b.person_name(), date_of_birth=dob)
        people.append(pid)
        return pid

    def pick_owner(reuse_p: float) -> str:
        if people and rng.random() < reuse_p:
            return rng.choice(people)
        return new_person()

    def split_stakes(k: int) -> list[Decimal]:
        if k == 1:
            return [Decimal(100)]
        cuts = sorted(rng.sample(range(10, 91, 5), k - 1))
        parts = [cuts[0]] + [cuts[i] - cuts[i - 1] for i in range(1, k - 1)] + [100 - cuts[-1]]
        return [Decimal(p) for p in parts]

    def base_business(seq: int) -> tuple[str, date]:
        bid = f"BUS-{seq:05d}"
        trade, naics = rng.choice(_TRADES)
        established = date(rng.randrange(1995, 2023), rng.randrange(1, 13), 1)
        customer_since = max(established, date(rng.randrange(2000, 2026), rng.randrange(1, 13), rng.randrange(1, 29)))
        customer_since = min(customer_since, date(2026, 6, 30))
        b.business(bid, b.business_name(trade), naics, rng.choice(_STATES), established, customer_since,
                   rng.choice(bankers))
        return bid, customer_since

    # Applicants.
    applicant_owners: list[str] = []
    for i in range(1, n + 1):
        bid, since = base_business(i)
        owner_ids: list[str] = []
        for stake in split_stakes(rng.choice([1, 1, 2, 2, 3])):
            pid = pick_owner(0.12)
            while pid in owner_ids:
                pid = new_person()
            owner_ids.append(pid)
            b.ds.add("registry.ownership", person_id=pid, business_id=bid, pct=stake.quantize(CENT))
        applicant_owners.extend(owner_ids)

        existing = Decimal(0)
        for _ in range(rng.choice([0, 1, 1, 1, 2])):
            orig = rng.randrange(40, 600) * 1000
            pay = orig * rng.uniform(0.012, 0.025)
            missed = rng.choice([1, 2, 3, 4]) if rng.random() < 0.04 else 0
            b.facility(bid, rng.choice(["term", "line"]), orig, orig * rng.uniform(0.2, 0.9), pay,
                       date(rng.randrange(2018, 2025), rng.randrange(1, 13), 1), missed)
            existing += money(pay)

        submitted = date(2026, 10, rng.randrange(1, 31))
        roll = rng.random()
        if roll < 0.05:
            kyc = ("lapsed", date(2024, 9, 1), date(2025, 9, 1))
        elif roll < 0.08:
            kyc = ("current", date(2025, 7, 1), date(2026, 7, 1))  # overdue but never marked lapsed
        else:
            reviewed = date(2026, rng.randrange(1, 10), rng.randrange(1, 29))
            kyc = ("current", reviewed, reviewed.replace(year=2027))
        b.ds.add("compliance.kyc", business_id=bid, status=kyc[0], last_reviewed=kyc[1], next_review_due=kyc[2])

        roll = rng.random()
        if roll < 0.04:
            b.adverse(bid, submitted - timedelta(days=rng.randrange(10, 350)))
        elif roll < 0.10:
            b.adverse(bid, submitted - timedelta(days=rng.randrange(400, 1500)))

        # DSCR spread: ~55% clear the approve line, ~20% in the band, ~25% below the floor.
        roll = rng.random()
        if roll < 0.55:
            target = rng.uniform(1.26, 2.40)
        elif roll < 0.75:
            target = rng.uniform(1.105, 1.245)
        else:
            target = rng.uniform(0.60, 1.095)
        injections = {}
        if rng.random() < 0.06:
            injections[rng.choice(dscr_window(submitted))] = money(rng.randrange(5, 60) * 1000)

        b.applicant(application_id=f"APP-{i:05d}", business_id=bid,
                    requested_amount=Decimal(rng.randrange(25, 900) * 1000),
                    term_months=rng.choice([36, 60, 84, 120]),
                    annual_rate=Decimal(str(rng.choice([0.0725, 0.0775, 0.08, 0.085, 0.09, 0.095]))),
                    submitted_on=submitted, purpose=rng.choice(_PURPOSES), existing_payments=existing,
                    target_dscr=Decimal(str(round(target, 4))), injections=injections,
                    opened=since, n_accounts=rng.choice([1, 1, 2]))

        banker = next(r["banker_id"] for r in b.ds.tables["relationship.coverage"] if r["business_id"] == bid)
        for _ in range(rng.choice([0, 0, 1, 2])):
            b.note(bid, banker, date(2026, rng.randrange(1, 11), rng.randrange(1, 29)), rng.choice(_NOTE_TEMPLATES))

    # Affiliates: customers that share owners with applicants. This is where
    # group exposure and related delinquency come from.
    for j in range(n + 1, n + 1 + max(1, n // 4)):
        bid, since = base_business(j)
        controller = rng.choice(applicant_owners) if rng.random() < 0.7 else new_person()
        control = Decimal(rng.randrange(51, 101))
        b.ds.add("registry.ownership", person_id=controller, business_id=bid, pct=control.quantize(CENT))
        rest = Decimal(100) - control
        if rest > 0:
            minority = rng.choice(applicant_owners) if rng.random() < 0.5 else new_person()
            if minority == controller:
                minority = new_person()
            b.ds.add("registry.ownership", person_id=minority, business_id=bid, pct=rest.quantize(CENT))
        for _ in range(rng.choice([1, 1, 2])):
            orig = rng.randrange(100, 1500) * 1000
            missed = rng.choice([1, 2, 3, 4]) if rng.random() < 0.15 else 0
            b.facility(bid, rng.choice(["term", "line"]), orig, orig * rng.uniform(0.3, 0.95),
                       orig * rng.uniform(0.012, 0.025), date(rng.randrange(2017, 2025), rng.randrange(1, 13), 1),
                       missed)
        b.ds.add("compliance.kyc", business_id=bid, status="current", last_reviewed=date(2026, 3, 1),
                 next_review_due=date(2027, 3, 1))
        b.cash_flows(bid, since, None, None, {}, 1)


def build(seed: int = 7, n: int = 1200, handbuilt: Path = DEFAULT_HANDBUILT) -> Dataset:
    spec = yaml.safe_load(handbuilt.read_text())
    b = _Builder(seed, spec["as_of"])
    _load_handbuilt(b, spec)
    _generate(b, n)
    return b.ds


def load_expectations(handbuilt: Path = DEFAULT_HANDBUILT) -> dict[str, dict[str, Any]]:
    spec = yaml.safe_load(handbuilt.read_text())
    return {a["application_id"]: a["expect"] for a in spec["applicants"]}
