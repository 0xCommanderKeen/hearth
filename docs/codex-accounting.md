# Synthetic run accounting

An internal `Hearth.admit(..., pricing_mode="standard" | "fast")` option pins the
Astra model, pricing mode and versioned API-equivalent schedule in the same SQLite
transaction as reservation, input digest and admission audit. Normal browser and
CLI admission still uses the fixed mock runtime; this option does not enable Codex.
Subscription and a future API backend share this calculation and the existing
$10/day allowance. Subscription estimates are not provider charges.

`Execution.finish_from_usage` requires the current owner and a sealed usage journal
bound to the admitted run, input digest and pricing pins. It locks the journal
while copying and validating its request receipts and terminal evidence. SQLite
stores that exact immutable copy and its checksum atomically with calculated cost,
artifact reference, task/run state, notifications and audit. A scalar mock cost
cannot settle a priced run. Unknown usage retains the normal admission hold;
contradictory usage refuses settlement. Explicit operator reconciliation remains
separately labelled and preserves the original unknown receipt.

Backups refuse unfinished priced runs. Settled receipts are self-contained in
SQLite: verification recomputes their binding, price, terminal status and artifact
hash without accessing an old worker journal or Docker. Restored stores stay held.
Authenticated run inspection exposes pricing pins and request token counts; the
browser labels the simulated amount as API-equivalent USD.

This is a synthetic accounting integration seam. The existing executor leaves
priced runs interrupted without dispatching them and continues unrelated mock
work. The contained mock adapter and its dispatch guard also refuse priced runs.
No production collector, credentials or model transport is enabled. A future
trusted worker must establish terminal process ownership and freeze collector
handoff; copying a journal alone does not prove those boundaries or account usage.
Tests use synthetic journals and real temporary SQLite. Real dispatch, cancellation,
collector isolation and daily-use acceptance remain under issue #69.
