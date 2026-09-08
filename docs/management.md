# Resident management

Townhall's Management page explicitly sets up Karen, her normal editable **Create
residents** library skill and its exact assignment. Repeating setup returns the
original receipt and preserves later operator edits. There is no startup seed and
no automatic management grant for newly provisioned residents.

The operator configures each resident's enabled grant, permitted configured runtime
profiles, named synthetic input sets, creation/work/routine capabilities, managed
resident count, child daily allowance, per-admission reservation, tool-call
limit and, with `send_letters`, whom this resident may write to. Empty input scope
permits empty inputs only. The existing household limits
and resident admission checks still apply. A daily allowance limits creation
configuration; later operator changes remain authoritative. Only authenticated
operator HTTP actions can change grants or household limits. Skill instructions
and model output cannot do so.

`GET /api/management` provides the operator catalog and the latest 30 durable
operation receipts. `POST /api/management/bootstrap` performs explicit setup.
`GET/PUT /api/residents/{id}/management` reads or replaces a grant; PUT requires
its exact `expected_revision`. Conflicts retain the browser draft until explicit
reload. Held restores expose this state read-only.

## Run authority and transactions

Admission pins an immutable grant revision/digest and a ten-minute expiry separately
from ordinary context version 7. Only admitted granted subscription runs select the
native management adapter; Reader retains its existing read-only exec adapter.
The trusted worker passes a private binding to the bridge, never model-visible
owner tokens, operator credentials, database handles or auth paths. Native thread
and turn identities are pinned once. Each call rechecks active run status, launch
intent, owner, epoch, declaration, admitted input, grant revision, expiry and native
session identities in the same SQLite writer as the application operation.
Revocation also refuses replay; editing or disabling a grant stops the old run's
authority and future use requires a new admission.

Not every native tool is a management power. A run whose declaration says
`memory_writable` receives the memory and journal tools described in
[resident memory](resident-memory.md) whether or not it holds a management grant, and a
granted run receives them even when its grant holds no capabilities at all; the operator
grant capability `writable_memory` separately governs handing that declaration to a
provisioned or reconfigured resident. A run admitted on a writable declaration alone is
pinned this surface with no grant behind it, is offered those three tools and nothing
more, and is reported as holding no management authority. A run working a letter is
pinned the same way and for the same reason: answering the question one was handed is not
management, so it is offered the reply tool whatever it is granted, and keeps it when a
grant it did hold is revoked mid-run. Reading one's own post follows having an end of a
letter rather than any capability, so narrowing a grant stops the next letter and never
hides the answer to the last one. A resident that has only ever been written to is
pinned this surface for that reading alone, with no grant and no writable declaration
behind it: a run pinned nothing is launched with no native surface and would be offered
no tool at all, whatever the scope says it may read. Writing to a colleague is management — it spends the
household's money on a resident the sender does not own — and the send tool appears only
under a grant carrying `send_letters`. The offered set is what the admission pins:
`tools_sha256` covers exactly the schemas that run may call.

The initial tools inspect bounded catalog summaries, exact skill revisions and
owned resident status; provision through the ordinary resident operation; and
assign/start work through ordinary queue and admission. Manager and creator are
derived from the run. Provisioning enforces all grant bounds and never grants the
child management access. Reuse can assign work to an existing resident managed by
the caller with a permitted profile/input selection. Foreign residents' catalog
summaries support duplicate avoidance but grant no work access.

Enabled routine creation must fit the ordinary scheduler's 10,000-microdollar
reservation as well as the requested immediate-work reservation. Resident status
inspection includes at most ten tasks with 500-character instruction excerpts and
explicit truncation flags. Catalog queries return at most 25 residents and 25 skills;
search narrows the result. Native UTF-8 serialization preserves complete legal
Unicode skill text. The bridge bounds the fully serialized native response
before committing effects; an oversized result returns a specific refusal and
rolls back the operation instead of interrupting the native turn.

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

## Writing to a colleague

`send_letters` is the grant capability that permits one resident to write to another, and
`letter_recipient_ids` optionally narrows that to a named set — at most twenty, checked for
shape rather than existence, so an operator may write the allowlist before the recipient
exists. It widens nothing else: the recipient's own declared `letters.accept` door still
has to be open, and a grant cannot open it. Neither capability nor door is created by
instruction text, and the two etiquette skills that say how to ask and how to answer are
ordinary library entries that grant nothing. Karen's setup carries `send_letters`, the
**Ask a colleague** skill, and **Answer a letter** seeded into the library for the operator
that opens a door to assign. [The letters contract](letters.md) records the guards, the
delivery path, the refusal table and the household settings, and
[ADR 0011](adr/0011-letters-between-residents.md) records the decision.

## Shared skill authoring

Karen receives the ordinary editable **Create good skills** library skill. The
`author_skills` capability permits creating and revising her own skills, requesting
bounded examples and publishing an exact passing candidate. `assign_skills` separately
permits exact active-revision assignments to currently managed residents. Neither
authoring authority alone nor a catalog read exposes a resident's assignment set.
`hearth_skills_assignments` requires assignment authority and returns the complete
ordered exact references and current assignment revision, so a manager can preserve
human choices and recover from a conflict before replacing the set. Neither
skill content nor an example grants capabilities. Humans use the same catalog and
conflict-aware revisions; editing an authored skill creates a draft with fresh checks.
Existing assignments retain their exact published revisions.

The examples are the requesting resident's own work. A validation pins that resident,
its declaration revision, the memory revision it had when it asked and the context
version this release builds, and its two cases are admitted on it: its allowance, its
reservations, its one run slot, under all household limits. A resident therefore cannot
watch its own examples run — they need the slot the requesting turn is holding — so it
reports the pending validation identity, ends its turn and reads the durable evidence in
a later one. An operator has no allowance or slot to lend, and names the resident that
runs them. Only the durable trusted task bindings permit pinning the exact draft; there
is no model-supplied draft override. Pending validation survives the requesting turn,
and future admissions recheck the current authoring grant and a one-day expiry.
Revocation prevents new admissions; already admitted work preserves ordinary holds.
Status waits last at most three seconds and release the database writer throughout.

An example run reaches no management tools at all: admission pins none for it, whatever
the resident is otherwise granted, and it carries exactly one candidate skill and one
case input beside the resident's own pinned context. An operator edit to the declaration
text the request pinned pauses admission, because the examples would then answer for a
different resident; restoring that text resumes the same queued case identities. Budget
and name changes are not that text and do not block. The resident's memory moves on
freely: each case carries the revision the request named, not whatever has been written
since. Unknown usage remains pending and never authorizes replacement execution.

The structural checker verifies populated sections and two bounded examples. Normal
and edge/adversarial cases execute the candidate using synthetic input revisions;
allowlisted output-length, required-phrase and forbidden-phrase assertions inspect
saved artifact bytes. Evidence records deterministic assertions, not semantic
assessment. There is no model grader or general
quality guarantee. Publication binds the immutable candidate/content/example hashes,
both successful case results, and a new active revision with identical instructions.
Backup verifies this chain; held restores remain read-only. Townhall shows draft
reasons, authorship, exact revisions, examples, accounted runs and publication proof,
and refreshes external changes while preserving conflicting unsaved human drafts.
