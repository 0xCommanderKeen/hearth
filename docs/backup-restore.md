# Backup and restore

Use unique destination directories outside the source data directory:

```sh
python -m hearth backup --data .hearth/local --destination /private/tmp/hearth-backup
python -m hearth verify-backup --source /private/tmp/hearth-backup
python -m hearth restore --source /private/tmp/hearth-backup --destination /private/tmp/hearth-restored
```

Capture excludes the executor with its existing lock, then holds a SQLite write
transaction while SQLite's backup API copies the database and the covered stores are
copied. Scheduler and API writes are therefore frozen too. A busy executor causes a
refusal; retry the command later.
A run that is priced but not yet settled refuses capture: its provider evidence is
still in flight and nothing here can copy it.
A backup is the household, not the directory it happens to live in: `hearth.db`,
`artifacts`, nested resident `memory` files, and the archived journal entries one
level below them in `memory/{resident}/journal`. Dotfiles, unrelated directories,
local development scaffolding beside the data, and credentials are not copied.

A versioned manifest records schema, package version, backend source fingerprint,
creation time, and every file checksum. Verification checks path shape, file type,
checksums, SQLite integrity/foreign keys, compatible schema and every referenced
artifact. Extra unverified payloads are refused, a directory outside the covered
stores included. Files are bounded to 128 MiB each;
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
the API disables its supervision loop on restored data. Browser reads, exact artifact
previews and the inbox remain available, and the client refuses mutation calls —
including marking a notification read.
There is intentionally no activation command or automatic hold removal.

The restored database retains runs (including active/unknown ownership), command
receipts, occurrence identities, usage and the inbox as it stood. This preserves
information for reconciliation without claiming that copied execution is
authoritative. The backup format does not include runtime credentials.
Production restore requires an ownership reconciliation plan and actual host checks
before activation.

Verification includes completed and active restores, matching artifacts and command
receipts, cancellation exclusion, API/client read-only behavior, corrupt/missing
artifacts, extra files, symlinks, FIFOs, traversal, busy workers and overwrite refusal.
An actual CLI demo → backup → restore rehearsal ran on synthetic data on 2026-09-06
under `/private/tmp/hearth-restore-rehearsal-{source,backup,copy}`. The copy has a new
epoch and remains held. Live disaster recovery and activation remain unproven.

## Current-schema backups

Only the current schema and complete layout are accepted. Incompatible prototypes
or modified schemas are refused without conversion. A backup pins the schema version
it was captured at, so re-capture after upgrading a store. Backups preserve every resident
memory revision and run pin, including unreferenced immutable files. Verification
rejects corrupt memory and references to another resident's memory. Journal entries, archived
references and their files are preserved the same way; verification rejects a changed
entry, a changed, renamed or missing archived file, an archived document that disagrees
with its row, and either half naming a run that belongs to another resident.
All restored copies remain held. There is no historical upgrade path or data importer.

Every run carries a runtime version and a well-formed input digest, and a run on the
runtime this release ships is verified against its stored provider receipt as well. A
run pinned to a runtime Hearth no longer ships is finished history: its evidence left
with that runtime, so beyond those two it is only required to have finished and to name
a kind Hearth actually shipped. A held restore preserves the store's recorded runtime;
it does not reactivate it.
