# Module responsibilities

`backend/` is the source root; `hearth` remains the installed package. The CLI is
`python -m hearth`; the ASGI factory is `hearth.app:from_env`.

- `app.py` composes the supported adapters and application services. `api/` owns
  strict HTTP payloads and bounded authentication middleware.
- `residents/` owns declarations, domain values and versioned memory. `work/`
  owns the transactional application service, task receipts/admission and routines.
  Resident declaration writes remain on that service so revision checks and
  admission serialize through the same SQLite authority.
- `inputs/` owns bounded synthetic note revisions, resident selections, explicit
  Reader seeding and ordered run input manifests; all mutations use the caller's
  SQLite writer. `skills/` owns the reusable skill library, draft authoring, bounded
  example evaluation, immutable publication evidence and exact assignments. Its
  example task bindings select exact draft/input revisions through ordinary admission
  on the requesting resident; they confer no model-visible draft override, and an
  example run is admitted with no management pin at all.
- `management/` owns operator grants, explicit Karen setup, admission authority and
  scoped tool dispatch. Its bridge calls provisioning/work writers in the same
  transaction as durable call/operation receipts and audit. Each provider's own
  transport remains in its own package — the app server's dynamic tools in
  `integrations/codex/`, the MCP shim and worker socket in `integrations/claude/`;
  configuration and receipt parsers do not own management policy. See
  [the permission contract](management.md).
- `execution/` owns run transitions, supervision, pinned context/staging, budget
  reconciliation and immutable usage persistence. Its usage module reads pins and
  validates provider evidence through `integrations/interface.py`; it does not
  interpret provider transcripts or rates. Receipt, cost, artifact and audit writes
  remain in the same Hearth transaction.
- `integrations/interface.py` defines normalized `Evidence`, the start/inspect/stop
  runtime protocol and the explicit supported-provider receipt/pricing boundary.
  Its `RUNTIMES` registry is the one place a runtime kind is described: whether it is
  live, which module answers for it, whether it settles from receipts, whether it can
  carry Hearth's own tools and on which transport, and what an operator calls it.
  Two kinds are live — the Codex subscription and the Claude subscription — and which
  one a resident runs on is that resident's own declaration
  ([ADR 0015](adr/0015-runtime-per-resident.md)), so one instance may hold several
  adapters and each run is worked by the one its admission pinned.
  It retains original receipts alongside normalized outcomes. Provider validation
  checks runtime assets/binary and input/model/schedule pins before settlement.
  Only provider-proven cancellation before launch bypasses launch-intent checks.
- `integrations/claude/` owns the Claude subscription: the bounded headless session
  and its detached worker, the stream-json parser and its pinned price schedule, the
  receipt reader, and the MCP shim and unix socket that carry Hearth's own tools into
  a session while authority stays in the trusted worker. The measurements it is built
  on are in [the Claude runtime](claude-runtime.md).
- `integrations/codex/` owns the Codex subscription — the first runtime — with its
  launch assets, process observation/termination, JSON events, request usage, pricing,
  receipt interpretation and the native management transport. `container.py` also holds
  the durable folder lock the runtime takes around launch; its Docker ownership, the
  pinned asset installer and the offline CLI fixture beside it serve only
  `scripts/probe-codex-subscription.py` and never the application. Detached workers call
  Hearth's dispatch guard; they do not acquire
  budget authority. There is no plugin registry or model folder, and no adapter is
  ever selected by a name written outside the registry. The
  fake runtimes the suite and the installed-wheel smoke inject live in
  `tests/fake_runtime.py` and are never packaged.
- `authority/` owns run-context credentials and shared household admission policy.
  `storage/` owns SQLite/schema, forward upgrades, artifact files and current-data
  backup/held restore. `observation/` owns snapshots and the inbox.

Dependency direction is application composition → services → integration contract
and storage. Provider parsers/pricing have no database access. Trusted runtime
workers necessarily use Hearth's persistence/dispatch services to recheck durable
ownership immediately around launch; this is not provider authority over policy.
The existing supported runtime names and version pins are unchanged.

The browser entrypoint loads `web/src/app/`, which also holds the inbox page; features
own residents, routines, tasks, skills, synthetic inputs, household policy, management
and Hamlet, including their tests. `shared/` owns the authenticated
client and text editor. Backend tests mirror owning modules; provider fixtures live
with provider tests. No unused future-provider packages are present.

This refactor changes Python import/worker paths, not schema, receipt JSON, URLs,
login storage, styling or model configuration. The isolated offline collector bundle retains its separately pinned flat namespace
and exact source bytes; host module moves are translated only while packaging it. Backup implementation fingerprints cover all nested Python
sources. Auth and data remain outside the checkout. Existing integrity-pinned
runtime assets continue to be verified against their exact source bytes; changed
assets are never silently accepted or rewritten.

Resident lifecycle and ownership are owned by `residents/lifecycle.py`, independently
of execution presence. `residents/maintenance.py` composes the normal configuration
owners in one revision-checked transaction. Operator API and scoped management tools
share this service; routine scheduling and actual launch consult the lifecycle owner.
See [resident maintenance](resident-maintenance.md) for transition and accounting rules.
