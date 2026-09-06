# Portable mock-state evidence

Issue #25 adds readable export and validation for a consistent Hearth mock backup.
Issue #27 adds isolated read-only import and verified repeat import. Neither operation
activates residents. The format is deliberately pinned to
schema 10; unsupported versions and schema changes require an explicit conversion.

```sh
python -m hearth backup --data /tmp/synthetic-hearth --destination /tmp/synthetic-backup
python -m hearth export-state --source /tmp/synthetic-backup --destination /tmp/synthetic-export
python -m hearth verify-state --source /tmp/synthetic-export/state.json
```

Use fresh destination names. Export never replaces an existing directory. It reads
a verified private copy of the backup, leaving the source unchanged. The output
directory is mode 0700 and state.json is 0600. Keep these archives outside Git:
they include task instructions, accounting evidence and complete artifact content.
The simulated flag identifies this format's scope, not an attestation about arbitrary
text somebody placed in the source database.

## Contract

The JSON envelope identifies `hearth-mock-state`, format version 1, database schema
10, `simulated: true`, the source observation epoch, table rows, and file contents.
Every operational table is included, including empty tables. Revisions, identities,
commands, queued work, active/uncertain runs, reservations, original accounting
timestamps, usage reports, both kinds of pause, approvals, pending effects, routines,
occurrences, audit sequence high-water mark and notification deliveries are retained.
UTF-8 mock store files retain their exact bytes and SHA-256 checksums, including
orphan files that may be evidence after a crash.

Two exclusions are explicit:

- Runtime credential records and each run's owner token are omitted. A later
  importer must establish fresh ownership through a separate transfer procedure.
- Process-local system metadata is omitted. The source epoch remains provenance;
  restore holds and observation epochs are local authority, not portable permission.
  Unknown metadata keys are refused instead of silently excluded.

Validation rejects unknown/missing envelope, table and column fields, duplicate
JSON keys, incorrect scalar types, broken relational constraints/references,
invalid current declaration/routine revisions, unsafe file paths, altered bytes,
missing referenced artifacts and invalid audit high-water marks. Input is bounded
to 32 MiB, 100,000 rows and 100,000 files. The SQL schema comes from the installed
Hearth code; no SQL from JSON is executed. Unknown backup schema objects or columns
are refused. Non-UTF-8 file stores require a future explicit format extension.

The reported semantic SHA-256 ignores row ordering and source observation epoch;
it changes when any exported operational value or file changes. It is a comparison
fingerprint, not a signature or proof of completeness relative to another system.
Checksums and structural validation do not establish source trust or authorize
execution. JSON fields containing audit, approval or runtime payloads are preserved
as evidence; this validator does not reinterpret their embedded domain protocols.

## Evidence and remaining work

Synthetic tests cover completed results, active cancellation, missing usage and
operator holds, uncertain publication with a receipt, routines, reconciled usage,
notification receipts, credential exclusion, source immutability, empty state,
pruned audit sequence preservation, stable comparison and malformed input refusal.

Still required: a detailed semantic diff, explicit compatibility mappings, memory/capability
models, ownership contention and rollback rehearsals. Cross-version conversion and
live migration acceptance are not established by these rehearsals. No HTTP export or
import endpoint is exposed, and no real data is used by the tests.


## Read-only import and reverse export

```sh
python -m hearth import-state --source /tmp/synthetic-export/state.json --destination /tmp/synthetic-import
python -m hearth backup --data /tmp/synthetic-import --destination /tmp/reverse-backup
python -m hearth export-state --source /tmp/reverse-backup --destination /tmp/reverse-export
python -m hearth verify-state --source /tmp/reverse-export/state.json
```

Import reconstructs the installed schema from validated values in a private staging
directory. A fresh observation epoch and durable `restore_hold` are committed with
the rows. Every run gets a new random owner token; no runtime credentials are
imported. Referenced files are checked before the directory is published. A failed
write removes staging state and leaves the destination unpublished.

The hold uses the existing database, API, supervisor, executor and broker guards.
Active and uncertain statuses remain evidence; they do not launch copied work.
There is no activation command. Import is not an ownership transfer, and passing
structural validation is not permission to execute embedded action payloads.

Identical semantic input may be retried at the same destination. A lock associated
with the destination serializes competing importers. The retry backs up the actual
copy and verifies its full reverse export against the input digest, in addition to
checking its import hold. A changed input, altered state, removed hold or symlink
destination is refused. No existing copy is overwritten. Reordered rows and a
different source observation epoch are semantically equivalent; a successful retry
returns the original copy's epoch and source provenance.

Backups now support held copies: the backup owns a SQLite reserved transaction for
a consistent read without using the operational write interface. This does not
remove the hold or enable any operational mutation. Reverse export through that
backup preserves the semantic digest; importing it elsewhere creates another held
copy with a distinct epoch. These tests establish Hearth mock round trips only,
not compatibility with another system or a rollback after live work.
