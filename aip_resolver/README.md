# AIP resolver patch

AIP 0.7.0 records a decision as answered by `typesafe` or `manual`. That is all its attribution
check allows (`aip.model.steps.RESOLVERS`), and the client writes `typesafe` for any injected
decision client. A model of our own therefore cannot be recorded under its own name, which is the
whole point of an audit trail.

`0001-resolver-attribution.patch` (against `zach-blumenfeld/aip` at
`7e63f2fd80acaa3138236b683de5d8b4b834ce35`, version 0.7.0) adds one resolver name, `endpoint`,
and has the client take the name from the decision client's `resolver_name` attribute. It is
offered upstream as a pull request. Until it lands, `src/brightwater/aip_patch.py` applies the same
two changes at import time, in the driver and in the server (`brightwater aip-server`), so this
repository does not carry a fork.

AIP is Apache-2.0; the patch is a modification under that license.
