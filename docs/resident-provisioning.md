# Resident provisioning

Townhall and Residents offer **New resident**. A complete setup includes purpose,
resident instructions, initial memory, ordered exact skill revisions, the
installation's configured execution profile, explicitly selected synthetic inputs,
a resident allowance, creator/manager provenance and optional daily routine/first
assignment. The result opens a profile with the configured input/profile and
provenance. Its queued first task has the ordinary **Start task** control. Creating
or accepting a task does not claim it has run.

The form opens with the resident allowance at **$1.00 a day** (1,000,000 microdollars).
It starts there because the allowance has to cover a day, and admission refuses
`budget_exhausted` once the day's spend, the runs still outstanding and the new
reservation would together pass it. The four real runs recorded in
[the journey evidence](karen-journey.md) cost 168,444 microdollars together, about 42,000
each: the previous $0.10 default covered barely two of them, and Karen's own default
reservation for work she assigns is 100,000, which spends such a day in one admission.
A dollar covers a day of that work. The number is a proposal and not a floor: the
operator edits the field before creating, and nothing refuses a smaller allowance.
`daily_limit` remains required on the API — the default lives in the form, not in the
backend — and the household allowance and the creating grant's `max_daily_limit` are what
actually bound it.

The authenticated operator contract is:

- `GET /api/resident-options`: configured execution profile, supported named
  synthetic example and named manager choices. There is no per-resident login or
  arbitrary provider/model/tool/environment configuration.
- `POST /api/residents/provision`, with `Idempotency-Key`: accepts `name`, `purpose`,
  `instructions`, `initial_memory`, ordered `skills: [{skill_id, revision}]`,
  `execution_profile`, `input_sets: [{input_set_id}]`, `daily_limit` (microdollars),
  `budget_timezone`, `creation_reason`, `manager`, optional
  `routine: {instruction, local_time, timezone, enabled}`, and optional
  `first_assignment: {instruction}`.
- `GET /api/resident-provisioning/{command_id}`: recover receipt and exact proposed
  setup, including a failed operation after restart.
- `POST /api/resident-provisioning/{command_id}/retry`: retry the same operator
  setup, preserving its identity and payload. Correcting the payload uses an
  explicitly new operation rather than changing the original request identity.
- `GET /api/residents/{id}/profile`: ready profile and immutable creator/time/reason,
  configured manager, originating run, profile and inputs. Resident names label
  actor/manager identities; stable IDs remain present.

The receipt returns `status` (`setup`, `ready`, `failed`), `reason`, stable command
and resident IDs, creator/manager/origin/time, routine/task IDs and the exact
`setup`. A failed setup is an inspectable outcome, not an active partial resident.
The UI shows setup progress, concrete failure reasons and same-operation retry.
Identical successful requests recover the original receipt without overwriting
later edits; changed content with an existing identity returns a conflict. The UI
retains an exact unconfirmed request for retry, but a definite rejection allows a
corrected draft under a new identity. A known ready receipt remains authoritative
if opening/refreshing its profile subsequently fails.

`Provisioning.create_in_transaction(db, command_id, request, actor=...,
originating_run_id=...)` shares its caller's writer. Future scoped bridges must
validate exact active-run credentials and grants in that SAME writer before
calling it; this helper is not a permission grant. Agent provenance must match a
starting/running, uncancelled resident run and its selected manager must be itself.
New children receive no management authority. The HTTP route derives `operator`
and rejects caller-supplied creator, timestamp or originating-run fields.

Creation reuses owning resident, memory, assignment and routine transaction
methods plus the normal task queue. Capacity is checked inside the creation
writer. A savepoint rolls back all partial resident/configuration/work changes on
a domain failure while retaining the operation and failure audit. Files published
before a rollback may remain unreferenced immutable content; they never confer
readiness. Process/transaction failure rolls back SQLite and a repeat reconstructs
one operation. Old generic declaration PUT is update-only: creating a new HTTP
resident must use provisioning. The explicit Reader example and internal test/demo
setup remain supported.

New residents explicitly choose up to four named synthetic input sets or no inputs.
No personal files or external connectors are offered. An empty selection yields
an empty notes context; see [synthetic inputs](synthetic-inputs.md) for revision
pinning and future-admission behavior.
All residents currently use the installation's configured execution profile.
Normal admission retains the shared household/resident budget and execution
controls; initial assignment creation performs no runtime launch.

Request transport is capped at 1.5 MB, including JSON escaping; a request exceeding
that aggregate cap must be shortened even if individual fields meet their limits.
HTTP 413 is a definite rejection and the browser keeps the draft editable. Memory's owning
interface enforces an actual UTF-8 byte limit of 128 KiB. Other declarations,
skill sets and routine/task instructions retain their owning bounds. Current-data
backup validates request identity, immutable provenance, ready-profile presence,
initial memory and optional work ownership. Held restore exposes the profiles,
requests, skill assignments and work while refusing all mutations.
