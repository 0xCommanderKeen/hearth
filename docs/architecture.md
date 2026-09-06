# Module responsibilities

`backend/` is the source root; `hearth` remains the installed package. The CLI is
`python -m hearth`; the ASGI factory is `hearth.app:from_env`.

- `app.py` composes the supported adapters and application services. `api/` owns
  strict HTTP payloads and bounded authentication middleware.
- `residents/` owns declarations, domain values and versioned memory. `work/`
  owns the transactional application service, task receipts/admission and routines.
  Resident declaration writes remain on that service so revision checks and
  admission serialize through the same SQLite authority.
- `execution/` owns run transitions, supervision, pinned context/staging, budget
  reconciliation and immutable usage persistence. Its usage module reads pins and
  validates provider evidence through `integrations/interface.py`; it does not
  interpret provider transcripts or rates. Receipt, cost, artifact and audit writes
  remain in the same Hearth transaction.
- `integrations/interface.py` defines normalized `Evidence`, the start/inspect/stop
  runtime protocol and the explicit supported-provider receipt/pricing boundary.
  It retains original receipts alongside normalized outcomes. Provider validation
  checks runtime assets/binary and input/model/schedule pins before settlement.
  Only provider-proven cancellation before launch bypasses launch-intent checks.
- `integrations/codex/` owns subscription and offline runtimes, launch assets,
  process observation/termination, JSON events, request usage, pricing and receipt
  interpretation. `integrations/mock/` contains the runnable inline/process mocks
  and contained-process rehearsal. Detached workers call Hearth's dispatch guard;
  they do not acquire budget authority. There is no plugin registry or model folder.
- `authority/` owns run-context credentials, permissions and approval-gated effects.
  `storage/` owns SQLite/schema, artifact files and current-data backup/held restore.
  `observation/` owns snapshots and notification delivery.

Dependency direction is application composition → services → integration contract
and storage. Provider parsers/pricing have no database access. Trusted runtime
workers necessarily use Hearth's persistence/dispatch services to recheck durable
ownership immediately around launch; this is not provider authority over policy.
The existing supported runtime names and version pins are unchanged.

The browser entrypoint loads `web/src/app/`; features own residents, routines,
approvals, tasks and Hamlet, including their tests. `shared/` owns the authenticated
client and text editor. Backend tests mirror owning modules; provider fixtures live
with provider tests. Empty future Skills or Claude packages are deliberately absent.

This refactor changes Python import/worker paths, not schema, receipt JSON, URLs,
login storage, styling or model configuration. Fixture packaging now recreates the
nested import namespace. Backup implementation fingerprints cover all nested Python
sources. Auth and data remain outside the checkout. Existing integrity-pinned
runtime assets continue to be verified against their exact source bytes; changed
assets are never silently accepted or rewritten.
