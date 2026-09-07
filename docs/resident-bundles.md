# Resident bundles

A resident bundle is one file, `<name>.hearth-resident.json`, that carries a resident's
**definition** so it can be shared and imported into another Hearth. It is not a backup:
nothing that happened to the resident travels with it. See
[ADR 0010](adr/0010-portable-resident-bundles.md) for the boundary.

## What a bundle contains

```
bundle_version: 1
source:     {resident_id, declaration_revision, memory_revision, exported_at}   # provenance only
resident:   {name, purpose, instructions, memory, daily_limit, budget_timezone,
             execution_profile, creation_reason}
skills:     [ {name, description, instructions, sha256} ]   # ≤8, ordered, exact pinned revisions
input_sets: [ {name, notes:[…], sha256} ]                    # ≤4, ordered current selection
routine:    {instruction, local_time, timezone, enabled} | null
management: grant policy | null                              # informational; never applied
```

Skill and input-set identities are per-database UUIDs, so a bundle embeds their exact
content together with the content digest the catalog already records
(`skill_revisions.sha256`, `input_revisions.sha256`). Left out on purpose: runs, tasks,
artifacts, usage, audit, lifecycle state, operation receipts, skill validation evidence.
Imported skills therefore arrive as unvalidated catalog entries. A resident with more than
one routine cannot be exported; provisioning accepts one.

## Export

- Resident → Care & continuity → **Export resident** downloads the file.
- `GET /api/residents/{id}/export` returns it with `Content-Disposition: attachment`.
- `python -m hearth export-resident --data <dir> --resident <id> --destination <file>`.

Archived residents export; a resident whose lifecycle is unavailable is refused.

## Import

- Residents → **Import resident**: choose the file, review the summary (skills, inputs,
  routine, memory size, exported profile, whether a grant was carried), optionally change
  the name and daily limit, then Import. Success opens the ordinary setup receipt.
- `POST /api/residents/import` with `Idempotency-Key`, body
  `{bundle, overrides?: {name, daily_limit, budget_timezone}, manager?}`. The response is
  the provisioning receipt plus `resolution`: for each skill and input set whether it was
  `reused` or `created`, the execution profile requested and used, and whether a grant
  was ignored. Status `201`.
- `python -m hearth import-resident --data <dir> --source <file> [--name N] [--daily-limit µ$] [--command-id K]`.

Import is ordinary provisioning with a materialisation step in the same writer:

1. The bundle is validated and every embedded digest is recomputed; a mismatch refuses
   the whole import (`bundle_digest_mismatch`).
2. Each skill is matched to an existing active catalog revision with the same digest whose
   skill is not archived; otherwise a new skill is created. Input sets match the current
   revision digest. Inner operations use ids derived from the import key
   (`import-skill:<hash>:<n>`), so an exact retry returns the same receipts.
3. The resolved request goes through `Provisioning.create_in_transaction`, so capacity
   checks, savepoint rollback, the durable `resident_provisioning` receipt, the generic
   retry route and the setup receipt UI apply unchanged. The installation's configured
   execution profile is always used; the bundle's is reported.
4. A management grant in the bundle is never applied. The imported resident starts with
   no management tools; the operator grants them explicitly afterwards if wanted.

Idempotency: an exact retry under the same key returns the same receipt; a changed bundle
under the same key is refused before anything is written. If provisioning itself is
refused (for example the household resident limit), the receipt is `failed` and the
materialised skills and inputs remain in the library, which the existing
`POST /api/resident-provisioning/{id}/retry` needs in order to replay. Catalog content
is shared library material and is never deleted by import.

Names are not unique; importing the same bundle twice under different keys creates two
residents that share catalog content. Use the name override to tell them apart.

## Limits

Bodies up to 1.5 MB on the import route; the bundle's own bounds are the provisioning
bounds (name 100, purpose 8 000, instructions 32 000, memory 128 KiB, 8 skills, 4 input
sets). `tests/fixtures/karen.hearth-resident.json` is Karen's exported definition and the
reference example: it carries her grant, which import demonstrably ignores.
