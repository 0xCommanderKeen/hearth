# Working on Hearth

Hearth is a private replacement for Warren. Read `CONTEXT.md` for domain terms and
`docs/implementation.md` for the current acceptance gate before starting work.

- Implement on a task branch and open a PR. Verify the branch immediately before
  every commit; implementation never commits or pushes directly to main.
- Track tasks with `status:ready`, `status:in-progress`, then `status:review` when
  their PR opens. Close only when the implementation lands in main.
- Run `make check` for backend changes. Add behavior tests at the owning module's
  interface for failure and concurrency guarantees; use real temporary SQLite.
- Keep operational mutations and audit facts in the same transaction. Unknown
  execution is a visible state, never evidence that retrying is safe.
- Read `docs/rebuild-plan.md` before changing ownership, persistence, runtime
  authority, or migration behavior. Record material departures in an ADR.
- Live data and credentials stay outside the repository. Use synthetic notes for
  development. Production ownership transfers require a concrete rehearsed plan.
- Update `docs/implementation.md` with verified results and remaining work at each
  milestone; tests alone do not complete a live migration or observation gate.

