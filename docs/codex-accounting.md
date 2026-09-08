# API-equivalent run accounting

Admission pins the Astra model and the versioned API-equivalent price schedule in the
same SQLite transaction as the reservation, the input digest and the admission audit.
`interface.pricing_pin` always pins the standard tier: the Codex subscription bills at
that tier, so a requested `pricing_mode` cannot change a schedule Hearth could not
honour. Subscription amounts are estimates against the existing $10/day allowance, not
provider charges, and a future API backend shares the same calculation.

Settlement requires the current run owner and a sealed usage journal bound to the
admitted run, its input digest and its pricing pins. The journal is locked while its
request receipts and terminal evidence are copied and validated. SQLite stores that
exact immutable copy and its checksum atomically with the calculated cost, the artifact
reference, the task/run state, the inbox notification and the audit fact. A bare scalar
cost cannot settle a priced run. Unknown usage retains the normal admission hold;
contradictory usage refuses settlement. Explicit operator reconciliation is recorded
separately as `operator_reported` and preserves the original unknown receipt.

Backups refuse unfinished priced runs. Settled receipts are self-contained in
SQLite: verification recomputes their binding, price, terminal status and artifact
hash without reading a worker journal or reaching Docker. A run pinned to a runtime
this release no longer ships left its receipts with that runtime, so verification
requires only that the run is finished and names a kind Hearth actually shipped.
Restored stores stay held. Authenticated run inspection exposes the pricing pins and
request token counts; the browser labels the amount as API-equivalent USD.

A trusted worker must establish terminal process ownership and freeze collector
handoff; copying a journal alone does not prove those boundaries or account usage.
Tests use real temporary SQLite and the fake runtime's provider-shaped journals.
