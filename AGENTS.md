# Working on Hearth

Hearth is a standalone project with new residents and fresh data. Read `CONTEXT.md`
for domain terms, `CHANGELOG.md` for what changed recently and `docs/implementation.md`
for the current acceptance gate.

- Implement on a task branch and open a PR. Verify the branch immediately before
  every commit; implementation never commits or pushes directly to main.
- Track tasks with `status:ready`, `status:in-progress`, then `status:review` when
  their PR opens. Close completed work when it lands; close cancelled work as not planned.
- Run `make check` for backend changes. Test failure and concurrency guarantees
  through owning interfaces with real temporary SQLite.
- Keep operational mutations and audit facts in the same transaction. Unknown
  execution is visible and never evidence that retrying is safe.
- Read `docs/rebuild-plan.md` before changing persistence, runtime authority or
  project scope. Record material departures in an ADR.
- Hearth's own stores upgrade forward. A schema change bumps `SCHEMA_VERSION` and
  adds fills for new required columns in `storage/migration.py`; a removed column is
  named in `DROPS`, a removed table in `DROPPED_TABLES`, a renamed table in `RENAMES`,
  a renamed column in `COLUMN_RENAMES`
  and a value the new layout refuses is rewritten through `REWRITES`,
  or the upgrade fails. An older store is
  rebuilt in place on start with the original kept beside it. Never require a fresh
  data directory for a Hearth version change (`docs/adr/0013-forward-schema-upgrades.md`).
  Do not import from other systems; definition-only resident bundles
  (`docs/adr/0010-portable-resident-bundles.md`) are the one cross-system path.
- Runtime kinds live in one registry, `integrations/interface.py`. Two are live: the
  Codex subscription, which every new store records, and the Claude subscription, which
  a store may be configured for. A store recorded against a removed runtime adopts the
  default on start, and finished runs keep their own pin because that is where the work
  happened. Which runtime a resident runs on is its own declaration
  (`docs/adr/0015-runtime-per-resident.md`): `system_meta.runtime_kind` is the default
  for residents that declare none, admission pins the resident's runtime onto the run,
  and one instance may be configured for several at once. Nothing in `execution/`,
  `work/` or `management/` names a provider. Tests and the installed-wheel smoke inject
  `tests/fake_runtime.py`; it is never packaged.
- Live data and credentials stay outside the repository. Use synthetic notes until
  real testing is explicitly selected; obtain concrete runtime/source decisions first.
- Record decisions that change scope, persistence or authority in an ADR and add a
  line to `CHANGELOG.md` per PR. `docs/implementation.md` is a checkpoint, not a
  per-PR ledger; update it only when a delivery gate changes. Fake-runtime checks do
  not complete real-host or daily-observation gates.
