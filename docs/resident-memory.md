# Persistent resident memory

Each resident has one Markdown memory note. An operator writes it; so can the
resident's own live run (see *Run-authored revisions* below). Reader reads its
pinned copy. This first workflow uses only synthetic text.

`Memory.save(resident_id, text, expected_revision=N)` serializes with admission and
other writes in Hearth's SQLite database. Revision zero means no prior memory.
Each successful save adds an immutable revision; the current revision is the
highest recorded revision. No separate current-file pointer or mirror is maintained.
An empty save is an explicit empty revision, not deletion of history.

UTF-8 bytes are preserved exactly, including newlines and whitespace, up to 128 KiB.
The file lives at `memory/{resident_id}/{sha256}.md`, with private directories and
mode-0600 files. Publishing syncs the file and directory entries before metadata
and audit commit. Existing identical content is reused; conflicting bytes under a
hash are refused. A failed database commit may leave an unreferenced immutable
file. A subsequent save can reuse it or publish different content without replacing
it. Backups preserve these orphan files as evidence; no pruning is implemented.

Reads verify size, checksum and UTF-8. Directory/file symlinks, nonregular files,
unsafe identities and missing/corrupt content are refused. Ordinary reads create
no memory directories. The data directory and its SQLite database remain trusted
operator storage; content hashes are integrity checks, not authentication.

Admission records a reference to the current memory revision in the same transaction
as the run, reservation and audit. No reference means the run admitted without
memory, even if memory is added later. Context version 6 contains that exact memory
revision and text. Later saves affect future admissions and do not change or revoke
an existing run's pinned memory. Cancellation, credential revocation and declaration
revision checks still apply. Authorized bytes already delivered cannot be retracted.

Missing/corrupt pinned memory prevents a new launch and leaves the run interrupted;
unrelated residents can progress. Existing runtime evidence is still reconciled
without rereading launch input. Cancellation can settle never-launched work at zero
usage; uncertain launch intent retains the existing recovery rules. MockRuntime
stores only the input digest and emits a fixed fixture, so the demo does not prove
model learning or useful memory-driven reasoning.

## Run-authored revisions

`Memory.save_from_run(db, run_id, text, expected_revision=N, operation_id=..., resident_id=...)`
is the run writer. The caller owns the SQLite writer, so the revision, its operation
receipt and its audit fact commit together. Authorship comes from the authenticated
run, never from the text: the revision records `author='run'` and the `memory.saved`
audit records `actor='run:<run id>'`. Operator saves record `operator` for both. The
128 KiB bound, the immutable `memory/{resident_id}/{sha256}.md` layout and the
`expected_revision` conflict rule are the operator ones; a run that loses a race with a
concurrent operator edit is refused with `revision_conflict` and overwrites nothing.

A run writes only the memory of the resident it runs for. A stated `resident_id` that
is not that resident, or a pinned memory row naming another resident, is refused with
`memory_run_mismatch`; an archived resident is refused with `resident_archived`. Only
current work writes: the same cutoff that governs run context reads
(`live_run`) refuses terminal, stopping, cancelled or superseded-declaration runs, and
an explicitly revoked context credential refuses too, both as `run_context_unavailable`.
Restored copies refuse every write.

Writes are idempotent on `operation_id` like management operations. The first call
records a receipt (resident, revision, checksum, author, operation and run identity)
in `memory_operations`; an identical retry replays it, reading the text back from the
immutable file rather than storing memory content in SQLite. A different payload under
the same `operation_id` is refused with `operation_conflict`. A refused attempt records
nothing, so retrying it is a fresh attempt.

## The declared capability and its tools

Writing memory from inside a run is a declared capability, not a management power.
Each declaration revision carries `memory_writable`, and the declaration revision the
run admitted with decides it. It is false for Reader and every ordinary resident, true
for Karen. A manager may declare it on a resident it provisions or configures only with
the `writable_memory` grant capability; raising it without that grant is refused with
`management_memory_not_permitted`, while resubmitting a value the resident already has
is the ordinary preserve-what-you-read edit and is allowed. An omitted flag in a
declaration save, a configuration change or the CLI keeps the current value, so a form
that never learned about the capability cannot withdraw it.

The tools live on the native tool surface, which today exists only for a run whose
admission pinned an enabled management grant. Capabilities are what a grant does *not*
have to hold: an enabled grant with no capabilities at all still receives the memory
tools, so writing memory is not a manager's privilege. But a resident with no grant has
no tool surface at all, so the pinned context reports `memory_writable` only when the
declaration allows it *and* this admission pinned that surface; the flag is the run's
actual authority to write, never an unkeepable promise. Giving an ordinary ungranted
resident these tools needs a native session that does not ride on a management grant,
which is not built.

A run that has both is offered three native tools beside whatever management tools its
grant permits:

- `hearth_memory_read` returns the run's own pinned revision as bounded 32,000-character
  pages (`offset`, `text`, `next_offset`), like the configuration reader. The revision
  cannot change under the run, so pages need no digest.
- `hearth_memory_save` calls the run writer above with the model-supplied `resident_id`,
  `expected_revision` and `operation_id`; the stated resident is checked against the run.
- `hearth_journal_write` writes this run's [journal entry](resident-journal.md).

The tools are absent from the declared tool set when the flag is false, and a call that
arrives anyway is refused with `memory_not_writable`. Because the offered set differs,
the pinned `tools_sha256` differs too: the tool schemas a run may use are fixed at the
same admission that pins its grant and its memory.

## Operator workflow

Townhall's **Memory** drawer explicitly loads the current note. Drafts survive
incoming snapshots and failed/ambiguous save responses. A newer memory revision
blocks saving until an explicit reload replaces the draft. Declaration edits do
not produce a memory conflict. Restored copies allow reading but refuse saving.

Authenticated `GET /api/residents/{id}/memory` accepts optional `?revision=N`.
`PUT` takes `text` and `expected_revision`; runtime credentials cannot use either
route. The write route permits bounded JSON escaping overhead for the 128 KiB byte
limit; other routes retain their existing request limits. Responses are no-store.
Only the memory revision, not its text, appears in ambient snapshots. Audit carries
revision, checksum and size, never memory content.

```sh
python -m hearth show-memory --data /tmp/synthetic-hearth --resident reader
python -m hearth save-memory --data /tmp/synthetic-hearth --resident reader \
  --source /tmp/synthetic-memory.md --expected-revision 0
python -m hearth show-memory --data /tmp/synthetic-hearth --resident reader --revision 1
```

The source is raw UTF-8 Markdown. Save/read use the same core validation as HTTP;
stale saves fail without changing memory. Resolve ambiguous responses by reading
back the current revision; do not automatically overwrite another edit.

## Backup evidence

Current-schema backups preserve every memory revision with its author, every run
reference and every file, including orphans. Verification refuses an operation receipt
that disagrees with its run's resident or names a revision no longer recorded as
run-written, so a consistently rehashed copy cannot relabel authorship. Layouts that
predate the `author` column are incompatible and refused, not upgraded. Verification rejects missing/corrupt content and references to
another resident's memory. Nested paths are checked without following symlinks.
Restore verifies before publishing a new held destination; new directory links are
synced before publication.

Synthetic tests cover concurrent saves, publication/commit failure, admission
rollback, original-input pinning, corruption/symlink/FIFO refusal, scope enforcement
and memory history through held backup/restore.
