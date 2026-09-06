# Mock publication approvals

The read-only Reader completes a synthetic summary as before. Townhall offers a
separate operator-driven rehearsal: enable mock publication requests, request a
review of a completed artifact, inspect its exact content, approve or deny, then
publish the approved summary to a local mock noticeboard. No resident receives
write credentials and nothing leaves the local data directory.

An approval binds the artifact identity and SHA-256, source run and source
configuration revision, current resident revision, grant revision, destination
revision, action kind, simulation marker, and exclusive expiry. The browser sends
the reviewed digest with the decision. First decision wins in a write transaction;
an expired request cannot be approved. A repeated request ID returns its original
request, including after revocation or expiry; different input is refused.

The broker rechecks expiry, current resident and grant revisions, destination
revision, and artifact bytes before recording dispatch intent. It passes the same
verified bytes to the adapter. Intent and its audit fact commit together. One
active action claims the destination; concurrent broker calls serialize on a lock
beside the canonical database path. Policy revocation serializes against dispatch
intent: a revocation committed before intent prevents dispatch. Revocation after
intent cannot undo an action already authorized for dispatch.

The approval ID is also the single durable action ID. The mock adapter writes an
immutable, synced local record. A matching receipt permits completion and advances
the destination revision in the same transaction as the audit record. A crash
before completion commit is recovered by inspecting the same receipt, even if the
grant has since been revoked: this records an existing effect, not a new effect.

Missing, mismatched, or unreadable evidence after dispatch intent leaves the action
unknown and retains the destination claim. Reconciliation only inspects evidence;
it never resends. This is deliberately conservative even for the local idempotent
adapter. A crash after intent but before publication can therefore require manual
evidence reconciliation. There is no reset/retry escape hatch that could duplicate
an uncertain non-idempotent action.

## Interfaces and verification

`Authority` owns versioned grants, exact requests, and decisions. `Broker` owns
consumption, destination claims, dispatch, and reconciliation. Only the broker
calls the mock effect adapter. Authenticated `/api/approvals` routes and Townhall
use these interfaces; snapshots carry the same persisted decisions and outcomes.

`tests/test_authority.py` covers simultaneous decisions and dispatchers, expired
and changed authority, digest mismatch, corrupted artifacts, request replay,
non-idempotent uncertainty, acknowledgement loss, receipt mismatch, destination
revision conflicts, audit rollback, and schema-3 upgrade. API tests cover the
operator journey and authentication; component tests cover preview before review,
exact digest submission, request identity retention, and unknown-state controls.

This establishes mock broker behavior only. Runtime-origin requests still need
short-lived scoped run authentication, actual-host isolation, and an adapter whose
credentials are inaccessible to residents. Approval notifications, an evidence
submission workflow for unresolved effects, and live runtime validation remain
later gates. Native visual QA remains pending browser permissions.

Recovery also verifies the checksum of the actual noticeboard content against the
approved artifact. A self-reported checksum in the receipt is not proof; missing
or changed bytes retain uncertainty and the destination claim without resending.
