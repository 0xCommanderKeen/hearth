# Build Hearth as a standalone project

Miha selected Hearth as product/repository, Hamlet as village and Townhall as
operator view, with a new read-only daily-summary Reader. The repository is public.
On 2026-09-06 Miha explicitly chose fresh data and removed migration from scope.

Keep the proven operational core and simplify it for the new workflow. Define the
current database directly; remove historical upgrades, portable import/export and
cross-system ownership transfer. Do not alter existing data to make it fit. Current
Hearth persistence, backup and held restore remain ordinary product requirements.

Other projects run independently. Their resident identities, data, capabilities,
usage and deployment are not requirements for Hearth. Synthetic notes are sufficient
for current development; source permissions, runtime/model, host and allowance must
be selected before a bounded real test. This decision supersedes the earlier staged
migration plan and its shared-registry decision.
