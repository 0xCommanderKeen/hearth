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
  adds fills for new required columns in `storage/migration.py`; an older store is
  rebuilt in place on start with the original kept beside it. Never require a fresh
  data directory for a Hearth version change (`docs/adr/0013-forward-schema-upgrades.md`).
  Do not import from other systems; definition-only resident bundles
  (`docs/adr/0010-portable-resident-bundles.md`) are the one cross-system path.
- One runtime ships: the Codex subscription. Every store and every run records that
  kind, and an upgrade retires runs recorded against a runtime that is gone. Tests
  and the installed-wheel smoke inject `tests/fake_runtime.py`; it is never packaged.
- Live data and credentials stay outside the repository. Use synthetic notes until
  real testing is explicitly selected; obtain concrete runtime/source decisions first.
- Record decisions that change scope, persistence or authority in an ADR and add a
  line to `CHANGELOG.md` per PR. `docs/implementation.md` is a checkpoint, not a
  per-PR ledger; update it only when a delivery gate changes. Fake-runtime checks do
  not complete real-host or daily-observation gates.
