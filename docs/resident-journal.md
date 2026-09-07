# Resident journal

A resident's journal is what its runs actually wrote: short dated entries kept apart
from the operator-editable [memory note](resident-memory.md). Hearth never writes an
entry on a resident's behalf, never edits one and never summarizes work into one.

`Journal.write(resident_id, run_id, text)` serializes with admission and other writes
in Hearth's SQLite database. A run has at most one entry: a second write from the same
run replaces its own text and date, keeps its place in the order, and is audited as a
replacement. Text is non-blank UTF-8 of at most 4 KiB. The writing run must exist,
belong to that resident, still be working, and not be cancelled; a settled, cancelled
or foreign run is refused. Archived residents are refused. Audit carries the run,
sequence, checksum and size, never the entry text.

Order is the per-resident `sequence`, not the clock, so entries written inside the same
second still read back in the order they were written. `Journal.read` returns the newest
first with `limit`/`offset` paging and the total of entries still in the database.

## Retention

The household policy keeps the newest `journal_limit` entries per resident, 30 by
default and 1–1000 by policy. Every write rolls what falls outside that bound into an
immutable file at `memory/{resident_id}/journal/{sha256}.md`, private directories and
mode-0600 files, then removes only that row in the same transaction as its audit fact.
Nothing is deleted: the file is the entry.

An archived file is the exact document

```
---
resident: reader
run: 6f1c…
sequence: 7
at: 1757203200
---
The entry text, byte for byte.
```

and is read back only if it re-serializes to itself under that resident's directory.
Existing identical content is reused; conflicting bytes under a hash are refused. A
failed database commit can leave an unreferenced archived file, exactly as memory does;
a later roll reuses it. Directory and file symlinks, nonregular files and unsafe
identities are refused, and the archive directory is never followed through a link.

A pinned entry is read back from its archived file when retention rolls it out during
the run that pinned it; nothing else reads archived entries into a context.

## The journal a run opens with

Admission pins the newest `CONTEXT_ENTRIES` (5) entries in `run_journal`, in the same
transaction as `run_memory`, and context version 6 carries exactly those under `journal`,
newest first, with `journal_usage` marking them as data that cannot grant authority or
override instructions — the same neutralization the pinned inputs carry. Entries written
later, including the run's own, join the next run's context, never this one's.

Each pin holds the entry's sequence, its text digest and the digest of the document
retention would archive it as, so a run that rolls its own pinned entry out mid-run still
reads the identical bytes back from the file. A pinned entry that is neither a row nor its
exact file leaves the run interrupted, exactly as missing pinned memory does; unrelated
residents keep working.

`hearth_journal_write` is the run's own writer, offered to a run whose declaration says
`memory_writable` (see [memory](resident-memory.md)). What a resident should write —
that it may keep durable facts in memory and closes its work with one short entry — is
skill text in the library, not wording in the context builder. That text is the shared
**Keep a journal** skill: write one short dated entry about what you did and what a
future you needs, save to memory only facts that will still be true next week, and never
invent an entry. It grants nothing and an operator may edit it. Karen carries it, and so
does a resident provisioned with writable memory.

## Operator surface

Authenticated `GET /api/residents/{id}/journal` accepts `limit` (1–100, default 20) and
`offset`, newest first, `no-store`. There is no operator write route and no runtime
route: runtime credentials are refused like every other operator path. The resident page
carries an explicitly loaded **Journal** panel — entries newest first, each with its
sequence, its date and a link to the run that wrote it — and says plainly when a resident
has none rather than filling the space. The run view says which entries a run opened with
and which entry it wrote. The
household `journal_limit` is settable through `PUT /api/household` and is preserved when
a client omits it, so the existing Townhall policy form cannot reset it.

## Backup evidence

Current-schema backups preserve every journal entry and every archived file, including
orphans. Verification refuses a changed entry (checksum or size), a changed or renamed
archived file, an entry whose run belongs to another resident, and unsafe paths, and it
reads back every run's pinned journal from its rows and archived files.
Restored copies read the journal and refuse writes.

Synthetic tests cover write/replace within a run, refusal after settling or cancelling,
concurrent writes from one run, a failed audit leaving only an orphan file, retention
rollover keeping its files, newest-first paging, the household bound, archive symlink
refusal, backup round trip and backup tampering.
