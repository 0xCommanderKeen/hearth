# Working on Hearth

Hearth is a standalone project with new residents and fresh data. Read `CONTEXT.md`
for domain terms and `docs/implementation.md` for the current acceptance gate.

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
- Start with new data. Do not add migration, historical compatibility or cross-system
  transfer machinery. Retain current-data persistence and backup/restore.
- Live data and credentials stay outside the repository. Use synthetic notes until
  real testing is explicitly selected; obtain concrete runtime/source decisions first.
- Update `docs/implementation.md` with verified evidence and remaining work at each
  milestone. Mock checks do not complete real-host or daily-observation gates.
