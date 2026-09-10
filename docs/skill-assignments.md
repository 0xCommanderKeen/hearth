# Resident skill assignments and run provenance

A resident profile distinguishes resident-specific instructions from reusable
skills. Attach a skill, choose its exact revision, order it with the arrow controls,
and save the assignment set. Detach removes it from future work. Publishing a new
catalog revision does not update residents: choose the new revision explicitly and
save. Skill detail lists assigned residents, their revision and position. Run history
shows the ordered names/revisions actually used, independently of today's assignments.

An assignment set has an independent optimistic revision. Assignment edits do not
change the resident declaration revision or invalidate already admitted runs. An
archived skill cannot be newly attached or upgraded. An existing exact assignment
may be retained/reordered and remains executable; the profile labels catalog archival.
Detaching and then reattaching an archived skill is refused.

`GET /api/residents/{id}/skills` returns current exact entries and catalog availability.
`PUT` at the same route takes `expected_revision` and an ordered `skills` list of
`{skill_id, revision}`. It requires `Idempotency-Key`; the actor is derived by the
operator route. Exact retries recover the durable receipt; changed requests or stale
assignment revisions conflict. The UI keeps a stale draft until explicit reload and
retains the exact pending request while a response is unconfirmed. `GET
/api/skills/{id}/assignments` returns the reverse index.

The `skills.assignments.save_assignments(db, resident_id, entries, *,
expected_revision, actor, command_id, now)` helper uses the caller's transaction. A
provisioning/management caller must authenticate the actor and enforce authority in
that same transaction, then insert the resident before applying its assignments.
It is not an authorization bypass or a runtime credential. The `Assignments` facade
owns a transaction for ordinary application calls. Catalog/assignment text never
changes model, tool, source or budget permissions.

Each set is bounded to eight distinct skills and 128 KiB of instruction UTF-8. It
stores a manifest count and SHA256 over the ordered identity objects containing
`skill_id`, `revision` and `sha256`, encoded with sorted keys and compact JSON
separators. SQL rows reference immutable catalog revisions. Admission validates the
entire set and copies its order, identities, content digests and manifest into
run-owned rows in the admission transaction, including an explicit empty set.
Missing headers, dropped/reordered rows and changed revision content fail validation.

Context version 7 is the single composition of purpose, resident-specific
`skill_text`, ordered `skills`, pinned memory, task and synthetic notes. The executor
and the scoped runtime route consume that one serialized context. A run
never joins current assignments or current catalog status into its input. Corrupt
skill inputs are refused before launch; snapshot/run inspection expose `skills_error`
without making all operator state unavailable. Unknown execution is not retry authority.

The supervisor reports scheduler errors separately from executor errors. A bounded
routine admission pass attempts healthy residents before reporting its first
unexpected refusal; corrupted work does not stall other queued or active residents.
Current-data backup checks every assignment/run manifest and catalog digest; held
restore preserves current assignments and original run provenance read-only. There
is no migration, historical import or automatic assignment upgrade.
