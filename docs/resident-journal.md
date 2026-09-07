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
mode-0600 files. The row moves with the text in the same transaction as its audit fact:
`journal_entries` loses it and `journal_archives` gains a reference to the file with the
same run, sequence and date. Nothing is deleted, and the text of an archived entry lives
only in its file — but a checked row still points at it, exactly as a memory revision
points at its content.

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

and is read back only if it re-serializes to itself under that resident's directory and
says exactly what its `journal_archives` row says. Existing identical content is reused;
conflicting bytes under a hash are refused. A failed database commit can leave an
archived file no row points at, exactly as memory does; a later roll reuses it, and until
then it is preserved evidence rather than journal history — nothing reads it back.
Directory and file symlinks, nonregular files and unsafe identities are refused, and the
archive directory is never followed through a link.

A pinned entry is read back through its `journal_archives` row when retention rolls it
out during the run that pinned it; nothing else reads archived entries into a context.

## The journal a run opens with

Admission pins the newest `CONTEXT_ENTRIES` (5) entries in `run_journal`, in the same
transaction as `run_memory`, and context version 6 carries exactly those under `journal`,
newest first, with `journal_usage` marking them as data that cannot grant authority or
override instructions — the same neutralization the pinned inputs carry. Entries written
later, including the run's own, join the next run's context, never this one's. Only rows
are pinned, so a run opens with the newest `min(CONTEXT_ENTRIES, journal_limit)` entries:
a household that keeps fewer than five is the tighter bound, and the archive is never
scanned to make the number up.

`memory_writable` in the same context says whether this run may write at all: the
declaration allows it and this admission actually pinned the native tool surface a
[memory tool](resident-memory.md) needs. A run without that surface still reads its
journal and cannot add to it.

Each pin names an entry by sequence and repeats what the run read: its writing run, its
time and its text digest. The entry is found in `journal_entries` while it has a row and
through its `journal_archives` row once retention rolls it out, and either way those three
values must still match, so a run that rolls its own pinned entry out mid-run keeps
reading the identical bytes. A pinned entry that is neither a row nor an archived
reference, or one whose text, run or time changed, leaves that run interrupted, exactly as
missing pinned memory does; unrelated residents keep working.

`hearth_journal_write` is the run's own writer, offered to a run whose declaration says
`memory_writable` (see [memory](resident-memory.md)). What a resident should write —
that it may keep durable facts in memory and closes its work with one short entry — is
skill text in the library, not wording in the context builder.

## Operator surface

Authenticated `GET /api/residents/{id}/journal` accepts `limit` (1–100, default 20) and
`offset`, newest first, `no-store`. There is no operator write route and no runtime
route: runtime credentials are refused like every other operator path. Townhall does not
show the journal yet; that is #119. The
household `journal_limit` is settable through `PUT /api/household` and is preserved when
a client omits it, so the existing Townhall policy form cannot reset it.

## Backup evidence

Current-schema backups preserve every journal entry, every archived reference and every
archived file, including orphans. Verification refuses a changed entry (checksum or
size), a changed, renamed or missing archived file, an archived document that disagrees
with its row, an entry or archived reference whose run belongs to another resident, and
unsafe paths, and it reads back every run's pinned journal from those same rows and
files. Both halves of a journal are therefore tamper-evident in the same way.
Restored copies read the journal and refuse writes.

Synthetic tests cover write/replace within a run, refusal after settling or cancelling,
concurrent writes from one run, a failed audit leaving only an unreferenced file,
retention rollover keeping its files, newest-first paging, the household bound, archive
symlink refusal, backup round trip, and backup tampering with an entry, with an archived
document and with either half's resident identity.
