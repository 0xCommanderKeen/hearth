# Persistent resident memory

Each resident has one operator-authored Markdown memory note. Reader can read its
pinned copy but cannot write memory. This first workflow uses only synthetic text.

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
memory, even if memory is added later. Context version 3 contains that exact memory
revision and text. Later saves affect future admissions and do not change or revoke
an existing run's pinned memory. Cancellation, credential revocation and declaration
revision checks still apply. Authorized bytes already delivered cannot be retracted.

Missing/corrupt pinned memory prevents a new launch and leaves the run interrupted;
unrelated residents can progress. Existing runtime evidence is still reconciled
without rereading launch input. Cancellation can settle never-launched work at zero
usage; uncertain launch intent retains the existing recovery rules. MockRuntime
stores only the input digest and emits a fixed fixture, so the demo does not prove
model learning or useful memory-driven reasoning.

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

Current-schema backups preserve every memory revision, run reference and file,
including orphans. Verification rejects missing/corrupt content and references to
another resident's memory. Nested paths are checked without following symlinks.
Restore verifies before publishing a new held destination; new directory links are
synced before publication.

Synthetic tests cover concurrent saves, publication/commit failure, admission
rollback, original-input pinning, corruption/symlink/FIFO refusal, scope enforcement
and memory history through held backup/restore.
