#!/usr/bin/env python3
"""Run one execution step of the brightwater-loan-assessment procedure.

stdin:  {"state": <the step's inputs>, "assets": {<stem>: <content>}, "expects": {...}}
argv:   the step name (gather | apply-policy | file | record-confirmation)
stdout: exactly the step's declared outputs, as one JSON object

The logic lives in the `brightwater` package (src/brightwater/procedure_steps.py)
so it is tested like any other code; the lending rules arrive as the
assets/policy.json this revision ships with.
"""
import json
import sys

from brightwater.procedure_steps import STEPS

payload = json.load(sys.stdin)
json.dump(STEPS[sys.argv[1]](payload["state"], payload.get("assets") or {}), sys.stdout, default=str)
