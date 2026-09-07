# Resident maintenance

The resident profile joins purpose, immutable setup provenance, current manager,
execution profile, inputs, exact skill revisions, budget, routines, memory and
recent tasks/results. The operator edits owning groups in one configuration form.
Configuration conflicts retain the draft for inspection; explicit reload replaces
it. An unconfirmed response locks the form and retries the exact command key and
payload. Confirmed commands return their immutable receipt across restart.

Provisioning records `setup` and `failed` on the setup operation. Failed setup is
inspectable/retryable through New resident and never publishes a partial active
resident. Committed residents have separate revisioned `ready`, `paused` or
`archived` lifecycle. Runtime presence remains independent: an archived resident
can still be running, interrupted or held. Invalid transitions fail; archive is
terminal. Reasserting the same state still records a new revision.

Pause suspends new manual admissions and routine occurrences. Already admitted
work may finish. Resume keeps every existing safety/accounting hold and enables
the normal newest-due routine catch-up rule. Archive additionally gates trusted
prelaunch dispatch. It neither cancels active execution nor settles uncertain
usage. Active/unknown runs retain reservations until ordinary explicit cancellation
or reconciliation. A known never-launched run may be explicitly cancelled at zero
usage; an unknown result is never inferred to be zero. Task/results history stays
inspectable through the archived-directory filter. Hamlet excludes archived homes
but separately links archived residents with unresolved runs.

`residents/lifecycle.py` owns lifecycle and current manager, with immutable
integrity-checked history. Provisioning's original manager remains setup
provenance, not authorization. `residents/maintenance.py` coordinates declaration,
memory, input selection, exact skill assignments and routines through their normal
owners inside one writer transaction. Every changed group carries its own expected
revision; all edits also pin the lifecycle revision. Any conflict or audit failure
rolls back database effects. Unchanged groups are omitted and existing run context
is immutable. Current-data backup validates lifecycle history and maintenance
receipts; no migration compatibility layer is introduced.

Operator endpoints read/write `/api/residents/{id}/configuration`, read/write
`/lifecycle`, and write `/manager`. Writes require authentication and an
`Idempotency-Key`. Transfer is operator-only, pins the lifecycle revision, preserves
creator/origin, and grants no new management tools. Capability grants retain their
own operator-only revisioned API. Directly seeded residents explicitly start with
operator ownership and no invented setup operation ID.

The managed bridge mounts `hearth_residents_configuration`,
`hearth_residents_configure`, and `hearth_residents_lifecycle`. Reads/edits require
`update_residents`; lifecycle requires `manage_lifecycle`; skill changes also
require `assign_skills`; routine changes require `routines` and the real scheduler
reservation within the grant. Profile/input/child allowance limits remain enforced.
Authorization resolves current ownership under the same writer as mutation and
audit. Transfer therefore immediately revokes the old manager's access. Managers
cannot forge another owner, transfer management or widen their own grants.

Configuration keeps existing owning limits: four synthetic inputs, eight exact
skills, 32 routine edits per operation, 32,000 instruction characters and 128 KiB
UTF-8 memory. The 1.5 MB authenticated transport cap accepts escaped legal Unicode
configuration groups while bounding oversized requests. Archived configuration
is read-only; operator ownership transfer remains possible for historical care.

Managed configuration reads return bounded JSON-text pages of at most 32,000
characters. Concatenate `text` in `offset` order and parse the completed JSON to
recover every original value and owning revision. Start at zero; every continuation
requires the returned `digest` as `expected_digest`. `next_offset: null` marks the
complete configuration. If any configuration group or lifecycle changes between
pages, `configuration_changed` requires restarting at zero, so a manager cannot
silently combine incompatible revisions. Page envelopes stay within the native
256 KiB response limit even for maximum Unicode values. Operator HTTP reads retain
the complete configuration response.

A due routine with unavailable/corrupt lifecycle is skipped without advancing its
occurrence. Snapshot exposes that resident's lifecycle failure; healthy due routines
continue in the same tick. A zero resident allowance is valid, including when editing
an unrelated profile field; ordinary budget admission rules still apply.

The managed configure request has the operator endpoint's same finite 1.5 MB
aggregate allowance, measured as UTF-8 JSON, with all per-group semantic limits
unchanged. Other management requests keep their 256 KiB bound. A manager can thus
read and edit a full legal Unicode declaration plus memory while preserving exact
unchanged values, not merely retrieve them.

The native incoming pipe permits a configure-specific envelope up to 2 MiB, only
for a structurally valid `item/tool/call` with configure parameters within 1.5 MB.
Other complete incoming records keep the 1 MiB limit. Tool replies remain bounded
at 256 KiB and the aggregate incoming transcript remains bounded at 4 MiB. The
exception changes transport capacity only; schema, revision, grant and ownership
checks remain in the application writer.
