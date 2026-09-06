# Run-bound Codex usage journal

`UsageJournal` records request intent before the trusted transport forwards a
generated request, then its terminal usage before delivering completion to the
CLI. Its binding pins run ID, admitted input digest, model, service mode and price
schedule. It never dispatches a request, supplies credentials or mutates a budget.

Files are exclusive, bounded and synced with their directories. Recovered reads
re-establish file and directory synchronization before trusting bytes left by a
failed write. Duplicate comparisons preserve JSON scalar types, including after
sealing; late contradictions invalidate subsequent estimates. A lock serializes
request intent, completion and sealing. An unresolved request or unknown prior
usage prevents another intent; reopening is observation, not permission to retry.
Identical response receipt replay is harmless; contradictory receipts leave a
permanent conflict marker. Unknown/orphan files, links, malformed data and changed
bindings refuse interpretation. Failed writes retain their partial evidence.

After independently observing CLI termination, the trusted worker supplies its
bounded JSONL and final file. Sealing retains that terminal observation before
interpretation, so a malformed observation cannot later be replaced by a more
convenient one. Successful output must agree with the final file. Known request
usage must agree with CLI totals before the pinned estimator can return a cost.
Missing counts stay unknown; a request lacking terminal usage cannot be sealed.
Reopening recomputes from request receipts instead of trusting a saved cost.

The [offline probe](codex-subscription-probe.md) uses this path for actual CLI
completion and injected tools, then verifies copied evidence outside the stopped
container. A third case interrupts the CLI while a request remains unresolved:
the journal survives container exit, has no estimate and refuses another intent.
The fixture handles warm-up and analytics locally; it forwards no provider calls.
These records establish completeness only for this controlled synthetic transport.

The fixture collector writes a dedicated persisted journal mount. All other code
mounts are readonly. This is deliberately fresh synthetic evidence, not Hearth's
database or a credential directory. The production collector must own its journal
outside generated capabilities; this fixture does not establish that isolation.
Operational wiring must pin the binding at admission, preserve receipts through
backup/restore, and commit usage provenance and accounting together with the run
and audit facts. It must also enforce dispatch authority and usage/budget limits.
Those remain prerequisites before real execution or budget settlement is enabled.
