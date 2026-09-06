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
`mock-noticeboard`. Dotfiles, unrelated directories, and credentials are not copied.
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

## Explicit upgrades of older backups

Format 1 began with schema 6. This binary supports backup schemas 6, 7, 8, 9, 10, 11 and 12;
unknown/future versions are refused. Verification checks an older backup's actual
tables, definitions, columns, indexes and foreign keys against the supported
historical layout, as well as its version, integrity, file checksums and references.
It never upgrades or writes to the source. Manually modified layouts are refused.

```sh
python -m hearth restore --source /path/to/older-backup --destination /path/to/new-copy --upgrade
```

Without `--upgrade`, older backups are refused. The option is valid only for
restore. A private copied database receives its durable hold and fresh epoch before
migrations run. Migration failure leaves no published destination and does not
change the source. After upgrading, schema and artifacts are verified again. The
original backup manifest is retained, and the result/hold record the source schema.
New budget timezones default to UTC for records predating that field; no costs or
holds are reset. These additive upgrades do not provide downgrade or activation.

Portable export still requires a current-schema backup. To export older evidence,
explicitly upgrade into a held restore first, capture that copy, then export its
current-schema backup. No implicit upgrade occurs during portable export.

Tests cover each supported older schema, active cancellation, missing usage,
operator holds, source immutability, explicit opt-in, incorrect layouts/version
claims, failed migrations and CLI behavior. The original schema-6 synthetic backup
from the earlier rehearsal also upgraded to schema 10 and stayed held; source file
hashes remained unchanged. This establishes mock compatibility, not live recovery.

Schema 12 includes resident memory revisions and run pins. Backups preserve nested
`memory/{resident_id}/{sha256}.md` files and verify every reference. See
`resident-memory.md` for exact-byte, orphan, path and held-restore guarantees.
