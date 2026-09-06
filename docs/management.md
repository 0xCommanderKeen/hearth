# Resident management

Townhall's Management page explicitly sets up Karen, her normal editable **Create
residents** library skill and its exact assignment. Repeating setup returns the
original receipt and preserves later operator edits. There is no startup seed and
no automatic management grant for newly provisioned residents.

The operator configures each resident's enabled grant, permitted configured runtime
profiles, named synthetic input sets, creation/work/routine capabilities, managed
resident count, child daily allowance, per-admission reservation and tool-call
limit. Empty input scope permits empty inputs only. The existing household limits
and resident admission checks still apply. A daily allowance limits creation
configuration; later operator changes remain authoritative. Only authenticated
operator HTTP actions can change grants or household limits. Skill instructions
and model output cannot do so.

`GET /api/management` provides the operator catalog and the latest 30 durable
operation receipts. `POST /api/demo/karen` performs explicit setup.
`GET/PUT /api/residents/{id}/management` reads or replaces a grant; PUT requires
its exact `expected_revision`. Conflicts retain the browser draft until explicit
reload. Held restores expose this state read-only.

## Run authority and transactions

Admission pins an immutable grant revision/digest and a ten-minute expiry separately
from ordinary context version 5. Only admitted granted subscription runs select the
native management adapter; Reader retains its existing read-only exec adapter.
The trusted worker passes a private binding to the bridge, never model-visible
owner tokens, operator credentials, database handles or auth paths. Native thread
and turn identities are pinned once. Each call rechecks active run status, launch
intent, owner, epoch, declaration, admitted input, grant revision, expiry and native
session identities in the same SQLite writer as the application operation.
Revocation also refuses replay; editing or disabling a grant stops the old run's
authority and future use requires a new admission.

The initial tools inspect bounded catalog summaries, exact skill revisions and
owned resident status; provision through the ordinary resident operation; and
assign/start work through ordinary queue and admission. Manager and creator are
derived from the run. Provisioning enforces all grant bounds and never grants the
child management access. Reuse can assign work to an existing resident managed by
the caller with a permitted profile/input selection. Foreign residents' catalog
summaries support duplicate avoidance but grant no work access.

Native call IDs replay exact results within a run. Mutations additionally require
an operation ID scoped to the managing resident, so a different call can recover
the same effect. Changed payloads conflict. Creation, first assignment/admission,
operation receipt and audit commit together; failed operations roll back their
effects and retain a refused call receipt. Audit failure rolls back the writer.
No uncertain execution authorizes a second launch.

## Native boundary and evidence

The adapter pins Codex CLI 0.153.4, exact `gpt-6-astra`, the generated configuration
catalog and declared tool schemas. Native transport owns configuration isolation,
typed tool requests, cancellation and original lifecycle/token events. The private
worker authorizes immediately around the turn write without waiting on the model
while holding a database writer. Execution limits are at most ten minutes and
64 tool calls, with smaller operator limits supported.

Receipts retain native events and pin binary/configuration/tools/thread/turn to the
admitted run. The shared subscription estimator settles validated native cumulative
usage once, separately from provider charges. Missing or invalid usage remains a
visible hold. Current-data backup validates immutable grant history, admission
bindings and runtime receipts; restore stays held and does not copy credentials.

Deterministic management tests exercise real temporary SQLite authority, concurrent
count limits, exact retries, refused escalation, revocation, ownership corruption,
audit rollback, reuse and held backup. The worker callback test uses synthetic
native events. These checks do not establish real provider or host isolation;
the actual pinned binary probe and bounded real journey are recorded separately
in the implementation checkpoint.
