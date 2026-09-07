# Residents write their own memory and journal

Status: accepted, 2026-09-07.

`docs/rebuild-plan.md` gave the operator the resident's memory: "The operator should be
able to define a resident's purpose, instructions, memory, limits and permitted sources."
Reader read its pinned copy and could not write. Epic #126 changes that: a resident's own
live run may add a memory revision and must write its own journal entry. This ADR records
the departure and the bounds it keeps.

**Two things, kept apart.** Memory is the durable note — facts that stay true — editable
by the operator and by the resident's own run, revisioned, one current revision. The
journal is what happened: one short dated entry per run, written only by that run, bounded
and rolled into immutable files, never edited by a person and never written by Hearth.
Neither is a summary of the other, and Hearth summarizes nothing into either.

**Authorship is recorded, never claimed.** Every revision carries `author`
(`operator` | `run`) taken from the authenticated writer, and a run-written revision is
tied to its run through the operation receipt that committed it. Nothing in the text can
change that, and a consistently rehashed backup that relabels a revision is refused.
Townhall shows the author beside every revision because the difference matters to a person
reading the note.

**A human edit always wins a race.** A run saves with the `expected_revision` it read.
A concurrent operator save is refused with `revision_conflict`, overwrites nothing, and
records nothing, so the run's next attempt is a fresh one that must read and merge. The
run writes only its own resident, only while its credential is live, and idempotently on
its `operation_id`.

**Writing is a declared capability, not a management power.** `memory_writable` sits on
each declaration revision, false for Reader and every ordinary resident, true for Karen.
Handing it to another resident is the separate operator grant capability `writable_memory`.
The pinned declaration revision decides for the whole run.

**The native tool transport is no longer management-only.** Before this epic a run reached
the native protocol only if its resident held an enabled management grant, so a resident
that could remember still had no way to write. Admission now pins a `run_management` row
for a writable declaration too, with no grant revision and no grant digest. Such a run is
offered exactly three tools — `hearth_memory_read`, `hearth_memory_save`,
`hearth_journal_write` — every management tool is refused with
`management_tool_not_permitted`, granting management afterwards cannot reach back into it,
and the operator surfaces never report it as management authority. Remembering is not
managing.

**The etiquette is library text, not code.** What a resident should write — one short
dated entry, only facts that will still be true next week, never an invented entry — is
the shared "Keep a journal" skill, editable in Townhall like any other. The context builder
states the capability and the pinned entries and says nothing about what to write. A
resident provisioned with writable memory receives that skill at its current revision when
the library holds it unarchived and the requested set leaves room inside the eight-skill
bound; the caller's own choices and their order are never displaced.

Consequences: `run_management.grant_revision` and `grant_sha256` are nullable, and backup
verification refuses a grantless pin whose declaration is not writable; the offered tool
set, and therefore the pinned `tools_sha256`, now differs three ways rather than two;
`memory_writable` is not carried by resident bundles, so an imported resident starts
without it; and a household that never sets Karen up has no "Keep a journal" skill to
attach. See `docs/resident-memory.md` and `docs/resident-journal.md`.
