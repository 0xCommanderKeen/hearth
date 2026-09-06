# Portable mock-state evidence

Issue #25 adds readable export and validation for a consistent Hearth mock backup.
Issue #27 adds isolated read-only import and verified repeat import. Neither operation
activates residents. The format is deliberately pinned to
schema 11; unsupported versions and schema changes require an explicit conversion.

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

The JSON envelope identifies `hearth-mock-state`, format version 2, database schema
11, `simulated: true`, the source observation epoch, table rows, and file contents.
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

Still required: explicit compatibility mappings, memory/capability
models and complete ownership/rollback rehearsals. Source-system conversion and
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

## Detailed comparison

```sh
python -m hearth diff-state --source /tmp/synthetic-export/state.json --against /tmp/reverse-export/state.json
```

Issue #29 adds `compare(before, after, limit=1000)` and this CLI command. Both inputs
must pass complete portable validation. Table rows are matched by their primary
keys, including composite revision/occurrence keys; row order does not matter.
Reports identify added, removed and modified records and show exact before/after
values for changed fields. File entries show their path, checksum and byte length,
without repeating file content. Source epochs remain visible as provenance but do
not affect equality. Embedded JSON strings compare as preserved text; the tool
does not infer equivalence between different action protocols or rewrite them.

The default output contains at most 1,000 changed records/files. `--limit` accepts
0 through 10,000. Totals and `change_count` always include all changes, and
`omitted_changes` makes truncation explicit. Limiting output never changes equality.
Ordering is deterministic: tables and identities, then file paths. Reports can
include private task/configuration/accounting text from changed database fields;
treat them with the same access controls as the input archives.

CLI exit status is 0 for equal state, 1 for valid but different state, and 2 for
invalid input or arguments. Validation occurs even with `--limit 0`. Comparison is
read-only and does not apply changes or authorize a transfer. Equal current-schema
Hearth exports do not establish compatibility with another system or prove that
source data excluded by this format has been migrated.

## Explicit version-1 upgrade

Format 1/schema 10 remains supported for verification and comparison with another
format-1 document. Import requires format 2/schema 11. Upgrade into a fresh directory:

```sh
python -m hearth upgrade-state --source /tmp/old-export/state.json --destination /tmp/upgraded-export
python -m hearth import-state --source /tmp/upgraded-export/state.json --destination /tmp/held-copy
```

The upgrade validates the old document before adding empty `skill_text` to every
declaration revision. All prior values, file content and source epoch are retained;
the original document is untouched. A new semantic digest includes the new format
and fields; the report also carries the source digest. Unknown old fields are
refused, never discarded. Cross-format comparison requires upgrading first. This
conversion neither recovers absent skills nor activates execution.
