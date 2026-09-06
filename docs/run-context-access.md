# Read-only runtime context credentials

`RunAccess.issue(run_id, owner_token)` is an internal executor interface. It issues
one current random bearer credential for a run, valid for at most fifteen minutes.
There is no HTTP issuance endpoint and no browser credential control. Rotating a
credential replaces its digest; revocation records a durable cutoff. Both changes
pair with audit records, which exclude the bearer and digest.

The database stores a keyed digest bound to the run owner and observation epoch,
never the raw bearer. Each read checks the digest, exclusive expiry, revocation,
current run state, cancellation flag and resident revision. Only starting/running
work can read; interrupted, stopping and terminal work cannot. Configuration changes
invalidate prior context access. Operator pause affects new admission and does not
revoke an already-running read-only task. Restored copies refuse runtime context
entirely, and their changed epoch also invalidates copied credential bindings.

The single allowed route is `GET /api/runtime/runs/{run_id}/context`. Runtime
authentication is separate from operator authentication and occurs before reading
the request body. A runtime credential cannot call operator reads, task commands,
approval decisions, publication, or another existing run's context. The operator
token cannot substitute for a runtime credential on this route. Malformed or
unrecognized runtime routes do not fall back to operator authentication.

Context contains only that run's identity, task identity, resident revision,
purpose, instruction, context version and explicit synthetic notes. The executor
and this route use the same pinned context reader; the route keeps its credential
checks in the same read transaction. It excludes run ownership
secrets, budgets, other residents, real notes and effect credentials. Responses use
`Cache-Control: no-store`; a valid read is authorized at its database snapshot.
Revocation cannot retract bytes from a read that was already authorized.

Tests exercise a mock runtime-origin HTTP client, exact-run isolation, credential
rotation/revocation/expiry, changed state/owner/configuration, audit rollback,
restart validity, secret exclusion and restored-copy refusal. This is the credential
contract for the read-only Reader. The deterministic in-process MockRuntime still
runs without a network credential, receiving the context directly as canonical
JSON at launch. Actual runtime credential injection, private credential
mounts, filesystem/network isolation and a real adapter contract remain pending.
No publication capability has been added to Reader and no real model is invoked.
