# Karen and shared skills within household policy

Status: accepted for implementation in issue #85, 2026-09-06.

The operator selected a new Hearth resident Karen that can create residents and
author reusable skills. This extends the earlier single read-only Reader scope;
it does not import residents or data from another system. Issue #95's module
organization is the prerequisite. Reader keeps its read-only execution profile.

SQLite remains the sole application authority. Shared skills have stable
identities and immutable Markdown revisions. Resident assignments name exact
revisions in an explicit order; runs pin skill and synthetic-input content at
admission. Editing a skill does not upgrade another resident's assignment.
Instructions describe behavior and never grant capabilities.

Browser actions and Karen's scoped tools invoke the same application operations.
Provisioning, skill publication, assignment and initial work use durable operation
identities, authenticated provenance and transactional audit. Retrying an operation
returns its original receipt; changed requests conflict. Uncertain model execution
never authorizes blind relaunch. Current-data backups and held restores preserve
these facts, without credentials or historical-schema conversion machinery.

Karen's management authority is explicitly configured by the operator and checked
against her active run. It bounds the residents she manages, execution profiles,
synthetic inputs and capabilities. New residents inherit no management authority.
Permitted operations proceed without an approval click for each item; refused
operations explain the applicable policy. The runtime bridge must not expose the
operator token, database, engine socket or subscription credentials to the model.
The concrete permission and bridge contract is delivered with issue #91.

All residents, including Karen, share the selected $10/day API-equivalent allowance
in Europe/Ljubljana, with finite resident-count and concurrent-run limits and
optional smaller resident allocations. Admission reserves shared and resident
capacity atomically. Unknown usage preserves its hold; replayed settlement does
not count twice. Only the operator may raise household limits or management grants.
Estimates remain distinct from subscription charges and a provider-enforced cap.

The acceptance milestone is one request to Karen that yields a suitable validated
skill, configured resident, synthetic inputs, routine and first saved useful result.
Deterministic checks and one bounded real Mac Codex-subscription/Astra journey
provide separate evidence. Pause/archive preserve history and truthful active or
unknown execution while stopping future admission. Personal-source connectors,
broad shell access, package installation, marketplaces, production deployment and
unrelated redesign remain outside this decision. Stop at the working demo.
