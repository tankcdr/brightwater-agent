"""The resolver patch to AIP 0.7.0 (Ch 6), applied at import time.

AIP's decision hook is TypeSafe-only in two places:

1. `aip.model.steps.RESOLVERS` is `("typesafe", "manual")`, and every
   recorded attribution is checked against it, on the client and on the
   server. An answer from any other model cannot be recorded honestly.
2. `aip.client.resolver.Resolver.resolve` writes `"resolver": "typesafe"` for
   every answer the injected decision client gives.

The patch adds one resolver name, `endpoint`, and makes `resolve` take the
name from the decision client (`resolver_name`) when it has one. The same
change as a diff against upstream is `aip_resolver/0001-resolver-attribution.patch`,
offered as a pull request so this module can be deleted once it lands.

Both the driver (`brightwater procedure run`) and the server
(`brightwater aip-server`) import this module, so the client records and the
server accepts the new attribution.
"""

from __future__ import annotations

from typing import Any

ENDPOINT = "endpoint"
_applied = False


def apply() -> None:
    global _applied
    if _applied:
        return
    from aip.client import resolver as client_resolver
    from aip.model import steps

    if ENDPOINT not in steps.RESOLVERS:
        steps.RESOLVERS = (*steps.RESOLVERS, ENDPOINT)

    original = client_resolver.Resolver.resolve

    def resolve(self: Any, pause: dict[str, Any]) -> dict[str, Any]:
        out = original(self, pause)
        name = getattr(self.client, "resolver_name", None)
        if name:
            target = out.get("attribution") or out.get("resolution")
            if target is not None:
                target["resolver"] = name
        return out

    client_resolver.Resolver.resolve = resolve
    _applied = True


def serve(argv: list[str] | None = None) -> None:
    """`aip server`, with the patch applied, so it accepts `endpoint` attributions."""
    import sys

    apply()
    from aip.client.cli import main

    sys.exit(main(["server", *(argv if argv is not None else sys.argv[1:])]))
