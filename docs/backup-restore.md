# Mock backup and restore rehearsal

Use unique destination directories outside the source data directory:

```sh
python -m hearth backup --data .hearth/local --destination /private/tmp/hearth-backup
python -m hearth verify-backup --source /private/tmp/hearth-backup
python -m hearth restore --source /private/tmp/hearth-backup --destination /private/tmp/hearth-restored
```

Capture excludes executor, action broker and notification workers with their
existing locks, then holds a SQLite write transaction while SQLite's backup API
copies the database and the known mock stores are copied. Scheduler and API writes
are therefore frozen too. Busy workers cause a refusal; retry the command later.
The allowlist is `hearth.db`, `artifacts`, `mock-runtime`, `mock-inbox`, and
`mock-noticeboard`, nested resident `memory` files, and the archived journal entries
one level below them in `memory/{resident}/journal`. Dotfiles, unrelated directories, and credentials are not copied.
This is the defined mock application layout, not a general filesystem backup.

A versioned manifest records schema, package version, backend source fingerprint,
creation time, and every file checksum. Verification checks path shape, file type,
checksums, SQLite integrity/foreign keys, compatible schema and every referenced
artifact. Extra unverified payloads are refused. Files are bounded to 128 MiB each;
larger backups require a deliberate limit change. Checksums detect corruption,
not a maliciously rewritten backup from an untrusted author.

Files are synced under a private temporary directory before the destination is
published. Existing destinations are never replaced. A crash after reserving the
destination name but before publication can leave an empty destination; verification
refuses it, and a subsequent rehearsal must use a fresh name. Backup/restore does
not overwrite live state or remove its source.

Restoring verifies again while copying, sets a durable `restore_hold`, and changes
the observation epoch. All ordinary database mutations refuse while this hold
exists. Executor entry refuses before inspecting/stopping/starting runtime work;
the API disables its supervision loop on restored data. Browser reads and exact
artifact/approval previews remain available, and the client refuses mutation calls.
There is intentionally no activation command or automatic hold removal.

The restored database retains runs (including active/unknown ownership), command
receipts, approvals, actions, occurrence identities, usage, queues, and mock runtime/
effect evidence. This preserves information for reconciliation without claiming
that copied execution is authoritative. The backup format does not include real
runtime credentials; those do not exist in the current
mock workflow. Production restore requires an ownership reconciliation plan and
actual host checks before activation.

Verification includes completed/active/uncertain-action restores, matching artifacts
and receipts, cancellation exclusion, API/client read-only behavior, corrupt/missing
artifacts, extra files, symlinks, FIFOs, traversal, busy workers and overwrite refusal.
An actual CLI demo → backup → restore rehearsal ran on synthetic data on 2026-09-06
under `/private/tmp/hearth-restore-rehearsal-{source,backup,copy}`. The copy has a new
epoch and remains held. Live disaster recovery and activation remain unproven.

## Current-schema backups

Only the current schema and complete layout are accepted. Incompatible prototypes
or modified schemas are refused without conversion. Backups preserve every resident
memory revision and run pin, including unreferenced immutable files. Verification
rejects corrupt memory and references to another resident's memory. Journal entries and
their archived files are preserved the same way; verification rejects a changed entry,
a changed or renamed archived file and an entry whose run belongs to another resident.
All restored copies remain held. There is no historical upgrade path or data importer.

Process-backed stores require all runs to be settled and workers idle before capture.
Active or uncertain process runs refuse backup without stopping the worker. Durable
request/result/cancel/started claims are checksummed and checked against pinned run
identity, input and results. Locks and fixture scratch files are excluded. Held
restore preserves the store's runtime choice; it does not reactivate that runtime.
