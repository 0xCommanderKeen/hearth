# External communications contract

Decision: [ADR 0019](adr/0019-external-communications.md), accepted 2026-09-11.
This is the implementation contract for #239, beginning with #240; it does not
claim a transport is installed or connected. Rollout still follows #233.

## Existing owners and the new seam

The current code at the architecture slice's base has schema version 13 and no
`channels/` package. These are existing interfaces, not a delivery implementation:

- `observation/notifications.py::record` records a unique kind/resource notice and
  audit fact in the caller's transaction. `Inbox` changes read status independently.
  `Forwarder.deliver(Notification) -> None` is an unused forwarding protocol;
  it cannot report durable delivery or prove exactly-once sending. The supported
  kinds are `run.succeeded`, `run.failed`, and `run.cancelled`.
- `work/service.py::Hearth.submit` owns an operator command transaction and calls
  `_queue_task`; it cannot be nested inside an inbound transaction. #137 must expose
  an owning transaction-taking submission seam that preserves command semantics,
  rather than write tasks from a transport or call `submit` inside a writer.
  `admit_in_transaction` remains the budget, concurrency and run admission owner.
- `management/bridge.py::BoundRun`, `authorize` and `Bridge.call` bind tools to an
  owner token, epoch, input digest, live run, declaration, grant, thread and turn.
  Tool dispatch and receipts currently execute inside a SQLite writer. Network
  history reads must therefore use a new prepare/perform/complete path, never
  insert HTTP into the current transactional `dispatch`.
- `management/authority.py::GrantPolicy` owns revisioned management, letter and
  mount grants. It grants no external communications today. New communications
  scope must be explicit and deny by default; provider tool availability grants
  nothing. `integrations/interface.py` remains exclusively the runtime registry.
- `storage/database.py` owns schema versioning and the writer's restore hold;
  `storage/migration.py` rebuilds Hearth stores forward. `storage/backup.py` copies
  validated current-schema data and restores a new epoch with `restore_hold`.

`channels/` will own connections, routes, conversation turns, origin-specific
communications grants/pins, cursors, delivery operations and attempts. `channels/`
composes Work, admission and the run bridge; `channels/discord/` only interprets
Discord and performs bounded REST requests. Observation continues to own notices.
There is no plugin framework or second runtime registry.

The shared service surface is small: revision-checked connection/route/grant
configuration; `submit_turn_in_transaction` for trusted inbound facts;
`enqueue_in_transaction` for typed outbound intent; prepare/complete methods for
reads and sends; bounded inspection and evidence-based reconciliation. Method
spelling may follow local conventions, but these transaction boundaries are fixed.
#137 owns routes/turns and #120 owns operations/attempts; #241 composes the worker.
They can implement independently against these identities and boundaries.

## Three records with different meaning

| Record | Source and authority | Payload and outcome |
| --- | --- | --- |
| Conversation turn/reply | Transport-verified inbound request; normal task and run | Bounded external text and reply, source message, task/run links; quiet, refused, failed or delivery state |
| Resident announcement | Explicit publication grant and operator/routine-origin run | Bounded authored text; source task/run/routine, pinned destination and operation |
| Operator notification delivery | Operator's notification forwarding configuration | Existing allowlisted kind/resource/local link only, source notification and operation |

Share attempt ownership and recovery, not an untyped notification payload. A run
can succeed while its reply is unknown or refused. A notification remains durable
and read/unread even if forwarding fails. Forwarding failures do not recursively
create more forwarded failure notices. Activation starts with new notices; deliberate
backfill must select a bounded range and retain the same notification/destination
operation identity. Changing a filter never resends a previously forwarded notice.

## Installation identity and authority

A connection has an opaque stable local ID, revision, transport, enabled state,
verified external bot identity and an opaque secret reference. The backend resolves
that reference against an operator-configured protected secrets directory outside
the repository, bundles, artifacts and run mounts. Admission and launch must add
that secrets directory to the existing protected-mount checks. It accepts no arbitrary path or
URL from a resident. Secret files are owner-only, reject unsafe links/permissions,
and never enter the database, logs, API, prompt or backup. Diagnostics return
missing/configured/invalid state, not values or filesystem contents. Non-secret
references and channel IDs are installation data in the database and backup, not
portable resident authority; bundle import requires explicit local rebinding.

A route has its own stable ID/revision, connection ID, resident ID, transport
address (Discord guild and channel IDs), sender policy and enabled state. Names
are display labels; rename never changes authority. Changing an address requires
an explicit new binding and invalidates pending work against the old revision;
queued sends retain their original destination. No automatic redirect or fallback.
Initial bounds are 16 connections/store and 32 routes/connection; defaults are
pending and closed until credentials, identity, scope and access are validated.

The first Discord connection is one dedicated bot assigned unambiguously to
Herald for explicitly selected guild text channels. At most one enabled inbound
route may match a connection/guild/channel. Shared-bot ambiguity refuses work.
Each resident grant separately lists read-history addresses, mention-trigger
addresses with reply permission, and publication destinations. An inbound address
without reply authority does not admit a turn. A read grant alone never starts work.
Publication is ordinary messages, including to a selected announcement channel;
Discord crossposting to followers is outside this version.

Two sender policies are supported:

- `operators_only`: explicit transport sender IDs chosen by the operator for
  private/operator routes. Matching an ID permits a bounded conversation, not a
  Hearth API login or implicit management capability.
- `guild_channel_humans`: any human member whose message mentions the verified bot
  in the selected guild channel may request a reply. This is the selected Herald
  policy; those people are not added to Hearth's operator allowlist.

The transport derives guild/channel from the authenticated request and validated
channel metadata, and author, message type, webhook/bot flags, timestamp and
structured mentions from Discord's response. An HTTP/tool caller cannot submit
those fields as trusted facts. Only ordinary human messages/replies qualify;
missing or inconsistent facts, bot/webhook/system messages, wrong destinations,
and ambiguous routes refuse or are ignored with a reason. Mention text, quoted
instructions and display names cannot prove identity. Unmentioned messages are
readable data only; the bot's own replies cannot create a feedback loop.

## Inbound turn, admission and scoped tools

The conversation identity is connection + route + source address + sender ID;
several people in a guild channel have distinct conversations, all still subject
to the resident's one-active-run rule. Source-message identity is independent of
route revision so editing a route cannot re-admit the same message.

A poll result reaches `submit_turn_in_transaction`. In one writer, recheck active
connection/route/revision, sender policy, freshness (300 seconds), resident
lifecycle and one open turn per conversation. Store accepted identity, bounded
text reference, conversation turn, task receipt and audit together, or a body-free
refusal/drop fact. Busy is refused, never queued behind that conversation's turn.
An accepted task uses normal admission, household/resident budgets, runtime and
usage accounting. A queued task is rechecked for current route/grant authority and
freshness before admission; expired or revoked work closes without running.

Admission pins immutable conversation context, external origin, route/grant
revisions, source address and effective capabilities onto the run. Permissions
are the intersection of the admission pin, current grants and origin ceiling:

| Origin | Communications ceiling |
| --- | --- |
| External conversation, including operator sender policy | Read/reply only within its source channel; no announcement or resident-management powers |
| Operator assignment or routine | Explicitly granted history and publication addresses |
| Other origins, including letters | No communications effect in this version |

A public request must not acquire management indirectly via assignment, routines,
letters, skill publication, writable mounts or another resident. Admission and
all tool dispatch paths enforce the origin ceiling, including a resident that
normally has those grants. The run also receives no unrelated input-set or folder
mount grants: read-only access to other household sources would still exceed the
source-channel ceiling. Its resident identity/skill text and bounded conversation
context remain inputs, not grants. Any independently retained own-memory/journal access
uses its existing declaration checks and is not a publication capability. Route
or grant edits never increase an admitted run's authority.

Read/announcement tools use exact-run authorization before receipt replay and
before preparing an operation: owner token, epoch, input digest, active launched
run, cancellation, declaration, expiry, thread/turn and capability pins. Reuse the
bridge's checks without making communications depend on management `enabled`.
A communications-only resident must be able to use its explicitly granted tools.
Before actual network dispatch, the worker rechecks the applicable current
route/grant and run authority. A read's completion checks the exact run again
before releasing bounded text; late or revoked reads cannot return data to it.
A queued announcement is an explicit durable publication handoff authorized by
that live tool call. It may dispatch while the run remains live or after observed
successful completion, validating the immutable intent and terminal provenance
as well as current grants and route. Failure or cancellation before the dispatch
permit refuses undispatched intent. Finishing normally therefore cannot race a
short successful publish task against worker polling. A finished run cannot
initiate a new operation or bypass live authorization by replaying a tool call;
the worker can only deliver the already committed intent.

Automatic replies are a distinct settlement handoff: after observed terminal
output, prepare a bounded reply and durable operation tied to that exact run and
turn. This may happen after the run is finished; the trusted worker validates its
terminal evidence, uncancelled outcome, immutable source destination and current
route/grant. It does not impersonate a live-run tool. Failed/cancelled turns do not
publish model output. Redact protected values before bounding, then prefix at
most once. Exact trimmed output `HEARTH_QUIET` means quiet, never a message to send.
No output or quiet closes the turn with its actual reason.

A turn remains open through work, reply preparation and delivery; it closes on
quiet, refusal/failure, confirmation or an explicit abandon/resolution. Unknown
reply delivery keeps that conversation visibly busy until resolved. Other
conversations/routes continue; this does not retain a finished run's budget hold
unless its runtime usage itself remains unknown.

## Polling and durable replay prevention

Inbound key: `(connection_id, external_channel_id, external_message_id)`, with
verified bot/account identity pinned to the connection. Store immutable first-seen
payload digest, decision, task/turn reference and timestamp. Exact replay recovers
that decision. Changed content under the same ID is an edit/conflict, never a new
request. Drops retain only IDs/digest/reason; no dropped message body.

Maintain a durable per-channel cursor and any in-progress page window. Initial
activation explicitly records a baseline at the latest observed message; older
history may be read but is not backfilled as requests. Reconnect never resets that
baseline. Fetch bounded pages, process numeric snowflakes chronologically, and
advance only across a fully processed interval in the same transaction as its
inbound decisions. Persist page progress when a backlog spans ticks; a later page
cannot skip older unprocessed messages. A failed channel does not advance; others
may progress. Transport tests must establish ordering across more than one page.

Dedup tombstones and cursor evidence outlive transcripts. Once an interval is
fully decided, body-free rejected IDs may compact into a durable contiguous
processed watermark; messages at/below it always refuse replay. Accepted task
links remain. Never compact unresolved gaps or reset progress on route edits.
Message deletion, retention, restart and a route being disabled/re-enabled cannot
make an old mention new work. A connection ID is never reused for another bot.

## Outbound intent, dispatch and recovery

An immutable operation contains kind, source identity, task/run or notification
reference, connection/bot/route/grant pins, exact destination, payload digest and
bounded payload. Idempotency keys are source-based: reply by turn ID, notification
by notice and destination binding, announcement by run and caller operation key.
Replaying the same key returns its operation; changed payload/destination conflicts.
Each attempt has its own ID, owner, store epoch, dispatch time, next eligible time,
result classification and external receipt. Never recycle a dispatched attempt.

1. Enqueue typed intent and audit in one transaction, with no network call.
2. The sole claimed worker prepares outside network I/O, then in a short writer
   verifies ownership/epoch, restore hold and current authority, records the
   attempt as `dispatching` and audits it. That commit is the dispatch authorization
   point. Only its exact owner may make this one request, immediately afterwards.
3. Perform bounded HTTP outside any SQLite writer. Store its confirmed receipt or
   classified failure/uncertainty and audit together in another writer. Late
   responses must match the attempt and immutable destination/payload identity.

Revocation and step 2 serialize through the SQLite writer: revocation first
refuses dispatch; dispatch first means the request may still leave Hearth even
if revocation commits before the first byte. This small interval cannot be made
atomic with an external service. Cancellation/revocation stops later dispatch
permits and safe retries; it cannot retract a permitted/in-flight or confirmed
request. UI and audit distinguish revocation from observed external outcome.

| State/evidence | Permitted recovery |
| --- | --- |
| Queued, never given a dispatch permit | Claim/recheck, cancel or refuse safely |
| Dispatching, acknowledgement absent after crash/timeout | Unknown; never automatically resend |
| Confirmed external message ID matching the attempt | Sent; immutable receipt retained |
| Definitive rejection or proved no request escaped | Failed/refused, or bounded safe retry with new attempt |
| Unknown | Evidence-based resolution or explicit abandon; no ordinary Retry action |
| Cancelled before dispatch | No send; retain intent/audit |

Even a crash after the permit commit but before HTTP is unknown on recovery.
An unlocked worker file, expired claim, absent message lookup, elapsed nonce window
or new epoch is not evidence that an earlier request never escaped. Ambiguous
5xx/disconnects are unknown unless the transport can prove rejection. Safe failures
use scheduled exponential backoff, at most five attempts per operation and no
sooner than the server's full required retry delay. Pending work does not block
healthy routes; Discord bucket/global delays apply across that connection.

The authenticated reconciliation owner accepts resolve-as-sent with matching
external evidence, resolve-as-not-sent only with affirmative rejection evidence,
or abandon (stop work while preserving unknown external outcome). If the operator
explicitly chooses a duplicate-risk reissue, record that acknowledgement and a new
linked operation; keep the original unknown evidence. Resolve with expected
revision, operator identity, time and reason in the audit. No resolution rewrites
run success, usage or the original attempt. A late conflicting receipt remains
visible and blocks unsafe retry; abandon is never labelled "not sent".

One worker holds the local store/connection lock; durable conditional claims
exclude competing local workers. Cross-machine consumers of a copied bot secret
cannot be fenced by SQLite. An installation binding records the active store epoch
and external bot identity; activation requires operator confirmation that the old
consumer is stopped. No automatic takeover of a copied connection. If that cannot
be established, keep it disabled and reconcile effects. This is a deployment
condition, not a claim that the service can detect every foreign bot client.

## Bounded text, visibility and preservation

Initial limits are 20 retained text turns per conversation and 10 rendered to a
run, each inbound text at most 8 KiB UTF-8, combined run conversation context at
most 32 KiB UTF-8 and 8,000 estimated tokens. Apply both limits, dropping oldest
whole turns first with explicit omitted counts. Cap retained transcript text at
1 MiB/route and age at 30 days; prune oldest eligible text. Pin active run input
until its owning run evidence may be retired. If protected active inputs exhaust
the route cap, refuse new turns rather than exceed it. History tools return at
most 50 messages/32 KiB with source IDs, timestamps, truncation and completeness
state; they neither crawl links nor download attachments.

Discord output is one message, at most 2,000 characters and 8 KiB UTF-8 after
redaction/prefixing; reject oversize announcements before enqueue and visibly bound
an automatic reply. No splitting in the initial implementation. If a later slice
adds splitting, each immutable chunk needs its own operation and partial/unknown
state; repeating the whole message is forbidden. Suppress implicit mentions.

Transcript pruning is not erasure of task/run history: instructions, pinned context,
artifacts and runtime receipts already have separate owning evidence lifetimes.
#137 should keep external bodies in bounded communications context and only safe
references in the task instruction where possible; it must explicitly disclose
any retained copies in run evidence. No promise of global 30-day deletion. Durable
cursors, accepted identity links, audit and operation/attempt metadata are retained;
terminal delivery bodies may be pruned after 30 days, retaining digest/provenance
and receipts. Unknown bodies remain for reconciliation within the bounded operation
size. Cap unresolved operations at 1,000 per connection and refuse new enqueues
when full; never evict unknown evidence to make room.

Authenticated bounded detail APIs may show external text and installation IDs;
ambient snapshots/SSE, notification payloads and audit contain references, counts,
states and reason codes only. Bound and escape display labels; do not copy raw
transport errors or headers into diagnostics. Redact known protected values from
stored/displayed reply and diagnostic text, and bound raw network parsing first.
External text remains least-trusted input even when it claims to be the operator.

Every schema-changing slice bumps `storage/database.py::SCHEMA_VERSION` and updates
`storage/migration.py` FILLS/DROPS/DROPPED_TABLES/RENAMES/COLUMN_RENAMES/REWRITES as
applicable. Existing stores gain empty disabled communications scope, never an
implicit grant, credential or fabricated send receipt. Preserve old residents,
runs, notifications and unresolved work. No fresh-store requirement or import from
other systems. This documentation slice changes no schema.

Extend current-schema backup validation for new foreign keys, typed payloads and
attempt/cursor invariants. Backups include retained text (sensitive installation
data), non-secret config, dedup/progress and delivery evidence; no secrets. A held
restore has a new epoch, may inspect records, and cannot poll, read remotely, admit,
claim, send, probe health or resolve effects. Activation is a separate explicit
recovery procedure after old-worker ownership and external uncertainty are checked;
restoring or acquiring a lock does not clear unknown state. No activation bypass
is introduced by this design.

## Backend network and Discord facts

The backend communications process holds secrets and performs all remote reads,
polls and sends. Runtime sandboxes retain their existing network/mount boundary;
residents receive scoped tool results through the existing private bridge. The
backend requires DNS/TLS access to the fixed Discord API origin. Production rejects
credential-bearing redirects and caller-controlled origins; only tests inject a
loopback endpoint. This adds backend egress, never broad Discord sandbox access.
Material changes to deployment egress enforcement require #183 and measured host
evidence; this ADR does not declare those gates complete.

Official references checked 2026-09-11: [Discord message API](https://docs.discord.com/developers/resources/message)
defines history and message receipts, bounded text and temporary nonce uniqueness;
[HTTP restrictions](https://docs.discord.com/developers/events/gateway#http-restrictions)
apply Message Content restrictions to REST too. General history cannot be assumed
complete from successful mention handling or empty content. The client respects
[rate limits](https://docs.discord.com/developers/topics/rate-limits), including
full bucket/global retry delays. REST polling needs no Gateway/public inbound
endpoint and may leave the bot appearing offline. Effective guild/channel access
must be validated and permission loss surfaced; names are never permissions.

## Delivery slices and acceptance

#137 implements model/admission and origin pins; #120 delivery and reconciliation;
#241 the shared process; #139 Discord; #242 history/announcement tools; #243 optional
notice forwarding; #247 shared UI/API/CLI and usage attribution; #244 integrated
Discord setup/evidence. #247 exposes the common `hearth communications` CLI for
list, inspect and explicit probe. #140 retains Telegram setup/etiquette/real
acceptance with #138, consuming the common UI. ntfy and Telegram are not Discord prerequisites.

Owning-interface tests use real temporary SQLite and credential-free loopback
fixtures: admission/revocation and competing workers; chronological multi-page
poll/restart/pruning; crash on both sides of dispatch; safe rejection versus lost
acknowledgement; grants/origin escalation and exact-run replay; bounded/hostile text;
existing-store upgrade and backup/held restore. Run full `make check` for each
implementation slice. Synthetic installed-wheel/Docker and browser journeys must
trace mention → task/run → reply and assignment/routine → announcement → delivery.

Actual bot/guild/channels, credential location, runtime/model, host and allowance
must be selected before any real journey. Independently verify a read, announcement,
human mention/reply and separate notification, including permission loss/revocation.
No live Discord connection, credentials or writes are required for this ADR. DMs,
Gateway, thread/forum discovery, crossposting, attachments, arbitrary HTTP tools,
moderation, roles and channel creation remain outside this epic. Real-host,
recovery, daily-observation and #233 rollout gates remain open.

## Shared model interfaces delivered by #137

Schema 14 adds installation configuration revisions, conversations, bounded turns,
body-free inbound decisions and run scope/context pins. Existing tables and input
digests are unchanged; ordinary context remains version 10 and conversation context
uses version 11. Upgrades add empty communications tables. Transcript retention
includes prepared reply text; pruning clears eligible terminal bodies while keeping
reply digests, source/task/run/operation links and inbound decisions. Active and
unknown turns are never evicted. Task instructions contain safe turn references;
pinned conversation inputs, runtime receipts and artifacts retain their own copies.

The backend composition surfaces are:

- `chat.config.Configuration.save`: revision-checked connection, route and resident
  grant configuration. Grants bind a connection plus exact guild/channel IDs. A bot
  identity has one connection; changing a route's resident, connection or address
  requires a new route binding. Credential files use canonical lowercase slots in
  a protected owner-only directory outside the store and repository. Initialize this
  configuration owner before admitting or launching work so the existing mount
  protections include that directory.
- `chat.service.Conversations.fetch`: calls an installed `channels.interface.Transport`
  outside a writer and issues an opaque `VerifiedTurn`. No HTTP or model schema
  accepts sender/mention/bot claims. `submit_turn_in_transaction` accepts only that
  service's verified receipt and composes `Hearth.submit_in_transaction`. `expire`
  closes queued stale or revoked work. Poll ordering/cursors and worker scheduling
  are supplied by #241/#139; every decided inbound ID already survives pruning.
- `chat.authority.check_scope`: after the existing bridge's exact-run authorization,
  checks pinned/current grants and the origin ceiling before a communications
  receipt replay or effect. The source conversation ceiling excludes management,
  letters, unrelated sources and all mounts. Own memory/journal authority remains
  independent. Ordinary communications pins require a proven operator command or
  routine occurrence; resident-authored assignments acquire no publication scope.
- `chat.reply.Replies.prepare`: reads successful terminal artifact evidence and stores
  one redacted bounded `ReplyIntent`, or closes quiet/failed/refused work. Quiet
  requires no credential. Redaction includes securely loaded connection credentials,
  known values retained in process memory and additional protected values supplied
  by the backend composition root; invalid unrelated connection files do not block
  a healthy route or erase already-known redaction values.
- `Replies.handoff_in_transaction(db, turn_id, enqueue)`: #120 supplies the typed
  `enqueue(db, ReplyIntent) -> operation_id` callback. The callback and the turn's
  operation link commit together; it must perform no network I/O. The delivery
  owner's `delivery_in_transaction` callback reports sent/refused/failed/abandoned
  or unknown, after its own evidence/reconciliation checks. Unknown stays open.

These interfaces are trusted backend composition, not new operator endpoints or
model tools. #120 owns actual durable operations and dispatch, #241 the process
and polling progress, #139 verified Discord facts, and #242 the read/publication
tool prepare/perform/complete wiring. No transport network call, real credential,
worker activation or delivery success is claimed by this slice.

## Durable delivery interfaces delivered by #120

Schema 15 adds operations, exact attempts, installation bindings, notification
forwarding configurations and operator resolutions. All are initially empty when
an older store upgrades; original Inbox rows and read state remain untouched.
Backup verification checks typed intents against historical source/grant/connection
pins, source dedup keys, payload digests, enqueue/permit audit identities and
attempt-bound receipts and resolutions. Restored bindings retain their original
path and epoch and confer no send ownership on the new held copy.

`channels.delivery.service.Delivery` is the trusted backend composition owner:

- `enqueue_in_transaction(db, ReplyIntent)` is the callback for
  `Replies.handoff_in_transaction`. Supply the same `Replies` owner to `Delivery`;
  reporting its delivery outcome happens in the result transaction. A missing
  settlement owner refuses and rolls back rather than strand a turn.
- `announce_in_transaction(db, BoundRun, operation_key, Destination, text,
  thread_id=..., turn_id=...)` authorizes the exact live call before replay,
  then pins its run, task, input, thread/turn, connection and grant. #242 must
  redact protected values before passing bounded text. The worker permits that
  committed handoff after successful terminal completion, but refuses failed,
  cancelled or revoked undispatched work and closes refused reply turns.
- `activate(connection_id, expected_revision=..., operator_id=...,
  old_consumer_stopped=True)` records an explicit installation handoff. It takes
  the same local file lock as the worker, so cannot steal a live local owner.
  A stopped worker's durable owner may be replaced, but every outstanding permit
  becomes unknown. Foreign consumers of copied credentials remain a deployment
  condition the operator must establish; local SQLite cannot fence them.
- `with worker(connection_id) as owner` holds the local lock through bounded I/O.
  `prepare(connection_id, owner)` commits at most one new permit and its audit,
  rechecking immutable/current authority. Pass its immutable intent to the adapter
  exactly once, outside the writer. Never cache/replay a permit. A worker exit or
  crash makes any remaining permit unknown, including a crash before HTTP began.
  `complete(permit, Receipt)` checks exact attempt/owner/epoch/digest identity and
  records external outcome independently of later revocation. Adapters return
  confirmed, affirmative safe failure/refusal, or unknown; diagnostics are bounded
  machine codes, never response bodies. Exceptions after permit return must be
  classified unknown unless the adapter has affirmative no-send evidence.
- Safe failures schedule a new attempt after exponential delay and the full server
  retry delay, up to five permits. There is no blocking wait and no retry for an
  unknown result. The unresolved operation cap is 1,000 per connection. #139 owns
  connection-wide HTTP bucket/global delays and #241 owns fair process scheduling.
- `inspect` and `detail` provide bounded body-free operator state and evidence.
  `resolve(..., expected_revision, operator_id, action, reason, evidence)` accepts
  sent/not_sent with an exact receipt for **every** uncertain attempt (a bounded
  list when needed), or abandon with the external outcome still unknown. An old
  rejection cannot resolve a newer permit. Explicit duplicate-risk reissue needs
  `duplicate_risk_acknowledged=True` and creates a linked operation while retaining
  original uncertainty. Cancellation only stops queued work. Contradictory late
  receipts remain separate facts, stop queued successors and require reconciliation;
  they cannot overwrite the original attempt. Unknown replies keep the conversation
  busy, including a late contradiction after a newer turn was already admitted.

`channels.delivery.notifications.Forwarding` replaces the old receiptless
`Forwarder.deliver(...)->None` contract. Configure an immutable destination binding
with revision-checked filters; `enqueue` selects a bounded audit interval and commits
intents and its cursor together. First enablement establishes the activation
watermark. Explicit bounded `backfill_after`/`through_cursor` selects older notices;
filter edits and backfill preserve source/destination dedup identity. Neither a
failed send nor a read/unread change alters the source notice or run, and delivery
failures never recursively record notification failures.

Notification-only transports use `Connection(transport="ntfy", bot_id=None, ...)`
and `NotificationDestination(connection_id, target_id)`. `target_id` is an opaque
installation slot, not a model-controlled URL or a pretend guild/bot. #121 supplies
the protected target resolution and bounded client; this slice performs no ntfy
I/O. Chat routes and grants refuse notification-only connections, and chat
announcements/replies continue to require real guild/channel shapes.

No process worker, poll cursor, HTTP adapter, model tool or operator HTTP endpoint
is activated here. #241/#139/#242 and the UI slices compose these trusted owners.
Tests use synthetic records and temporary SQLite, including actual worker-process
exit, held backup while a permit is outstanding and v14 forward upgrade. These
checks do not complete a live connection, host recovery or daily-observation gate.

### Shared worker seam delivered by #241

`channels.worker.Worker` composes Conversations, Replies and Delivery beside the
runtime supervisor in the application lifespan. Its store lock and each activated
`Delivery.worker` connection lock live until bounded I/O and cleanup finish. An
injected factory constructs local clients only (no HTTP in constructors); the
production adapter registry is empty until a transport slice installs one. No
implicit credential directory or connection activation is introduced. Held stores
refuse the worker before client construction, secret lookup, polling or admission.
`supervise=False` starts neither worker.

`channels.polling` defines `latest`, `poll` and `send`: authenticated backend
adapters enforce a 10-second request deadline and 512-KiB response cap, and return
at most 50 verified messages per page. IDs are canonical nonnegative decimal
strings. A poll receives exclusive `after` and inclusive fixed `through`; it must
return the oldest messages in strictly ascending numeric order. `Page.complete`
asserts the whole remaining interval was examined, including deleted IDs. A
newest-first API page is insufficient; #139 must establish correct traversal with
more than one full page. An empty channel has latest ID `0`. A disappearing newest
message cannot rewind a durable watermark.

Schema 16 adds initially empty `communications_cursors` and
`communications_schedule`. Baseline activation records the latest observed ID
once per actual connection/guild/channel. Every later window persists its upper
bound before page fetch; decisions, conflict evidence and page progress commit in
one writer. Restart resumes the same window. Existing immutable inbound receipts
win replay even after text pruning; changed payloads retain body-free digest/audit
conflict evidence and cannot block later page decisions. Previously processed
intervals also refuse unrecorded older IDs. Configuration revisions and current
grants are checked again when the fetched page commits.

Each pass rotates at most 16 routes, 50 queued admissions and 50 terminal replies;
expiry checks at most 100 queued turns, and delivery claims at most one operation
per configured connection. Ordinary admission uses the existing 10,000-microdollar
reservation and owning budget/concurrency/freshness checks. Terminal failures and
cancellations close under settlement; quiet/no-output replies close under Replies.
Immutable reply handoff and exact delivery permits remain #137/#120's owners.

`RetryLater(seconds, connection_wide=...)` schedules poll retry without sleeping.
Durable deadlines only move forward, including during credential failures. A send
returns `Receipt` for a destination limit, or `SendResult(receipt,
connection_wide=True)` for an account/global limit. The worker persists its
`retry_after` deadline before completing the receipt (including exceptions), then
passes durable destination exclusions to `Delivery.prepare` before any new permit.
Other destinations remain eligible. Adapter
constructors and `close` manage local caches; references and secret bytes reload
on each pass, including while waiting, so removal invalidates old clients promptly.
All external permission checks belong to the authenticated transport. Send adapters
return exact typed receipts, make one request and never retry internally; a raised
exception is unknown evidence. `safe_failure` is permitted only when the adapter
can establish that no external effect occurred. Worker death likewise recovers
outstanding permits as unknown and never repeats them automatically.

Temporary SQLite/loopback and installed-wheel checks exercise this composition.
They neither install a real transport nor complete host, credential, deployment,
or daily-observation acceptance.

### Discord transport delivered by #139

`channels.discord.Discord` is the production worker's local factory. It takes only
backend connection configuration and resolved secret bytes; construction and cached
health reads make no HTTP calls. Setup must still supply the protected `Secrets`
resolver and activate installation ownership; the application does not invent a
credential directory. `supervise=False` and held stores remain inert. The private
`_test_origin` injection accepts only `http://127.0.0.1:<port>/api/v10`, is never an
operator/model schema and is used exclusively by synthetic tests. Production uses
fixed `https://discord.com/api/v10`, bot authentication and a descriptive User-Agent;
HTTP redirects are never followed and environment proxy settings are not consumed.

The client rechecks bot identity, selected channel/guild binding, supported channel
kind, member roles and channel overwrites before each operation. View/history/send
requirements are independent. Announcement channels accept ordinary plain messages;
there is no crosspost endpoint. Structured mentions, author bot/system flags,
webhook presence and ordinary message types (0 and 19) supply inbound facts; the
shared conversation owner still applies routing, freshness and ordinary admission.

`history` returns at most 50 messages and 32 KiB including message, channel, guild,
author and timestamp provenance. Its completeness/truncation and Message Content
state are separate from channel permission. Application Message Content flags are
checked deliberately on reads; successful mentions do not establish general-history
access. Empty history with verified permissions is not diagnosed as permission
failure. #242 still owns exact-run prepare/perform/complete authorization around
this backend-only method. Cached worker health shows safe transport/content states;
ordinary UI refreshes never authenticate or read channel history.

[ADR 0020](adr/0020-discord-reverse-scan-progress.md) records the body-free reverse
scan frontier and inclusive examined-prefix extension to `Page`. Schema 17 preserves
existing baselines/windows and starts old cursors with a null frontier. At most one
history page is fetched per poll call. API requests have a cumulative ten-second
deadline across DNS, preflight and message fetch, a socket interrupt covering
trickled headers/body, bounded HTTP parsing and a 512-KiB body cap. At most four
resolver-only background threads may wait for the system resolver; timed-out DNS
work cannot connect or send HTTP. Resolved addresses retain the original hostname
for TLS SNI and certificate checks. No attachment or linked URL is fetched.

`take_limits` exports observed bucket/global delays, including successful exhausted
responses. The worker writes channel/guild/connection deadlines before another
scheduled pass or completion of a send receipt; destination exclusions prevent
minting a dispatch permit while that scope is waiting. Full server delays are kept,
including waits longer than 30 seconds. Credential and permission failures use a
five-minute cooldown; malformed/transient read failures retain bounded retries.

Delivery's permit includes the owning reply's source message ID. A send makes one
POST with all implicit mentions suppressed, optional source-only reply reference,
and a stable operation nonce with `enforce_nonce`. Confirmation requires the returned
message identity, nonce, content and reply reference to agree. This does not turn
Discord's temporary nonce window into durable exactly-once evidence. Lost or
inconsistent acknowledgements stay unknown and are never automatically resent.
Failures during read-only preflight are affirmative no-send evidence. No splitting,
attachments, general HTTP tool, scoped resident tools, forwarding UI or live setup
is included here.

The official contracts were rechecked for this implementation:
[Message API](https://docs.discord.com/developers/resources/message),
[REST content restrictions](https://docs.discord.com/developers/events/gateway#http-restrictions),
[rate limits](https://docs.discord.com/developers/topics/rate-limits),
[permission calculation](https://docs.discord.com/developers/topics/permissions),
[application flags](https://docs.discord.com/developers/resources/application), and
[guild/member metadata](https://docs.discord.com/developers/resources/guild).
SQLite/loopback regressions cover chronological multi-page/restart/failure progress,
ordinary human task/run/reply, sender exclusion, permission/content refusals,
credential/origin/bounded parsing, full global/guild/channel waits, uncertain sends,
forward upgrade and held backup. No Discord credential, real ID or live host was used;
these checks complete no real-source or daily-observation gate.

### Run tools delivered by #242

[ADR 0021](adr/0021-run-communications-tools.md) records schema 18's durable native
communications request seam. Both native runtime transports offer
`hearth_read_channel_history` and `hearth_publish_announcement` for communications
origins. Their presence grants no authority: every call, replay, read permit and
read completion checks the exact live run and origin-specific current scope.

History prepares a queued operation. Repeat its operation ID with the same arguments
to retrieve its completed bounded result; changing arguments conflicts. Two requests
per worker pass rotate independently of polling and delivery. A reading operation
interrupted by restart returns an explicit refusal instead of silently fetching a
new page. Missing Message Content access refuses rather than presenting empty text
as complete. History includes provenance, cursor, truncation and permission state;
URLs and attachments are never fetched. The 32-KiB bound includes the escaped
native result envelope. If one source message cannot fit, the result names the
omitted message count/reason and a cursor that advances past it.

There are 32 distinct communications call IDs and at most four 2,000-character
announcements per run. Announcements use Delivery's immutable intent/attempt records;
queued, confirmed and unknown are distinct from task outcome. Run-owner values are
redacted before enqueue. Worker-known credentials in immutable publication text
refuse before HTTP; history text is redacted before it is retained or returned.
The run bridge receives neither connector secret nor secret resolver. Request/result
text is retained as run evidence, separately from transcript pruning. Current backup
validation preserves these pins and receipts, and held restores remain inert.

The [editable Herald etiquette](skills/herald-etiquette.md) can be copied into the
Skills library. Installing or assigning it never connects Discord or grants a route.
All acceptance here is synthetic; live setup and real-host acceptance remain #244.

### Operator notice forwarding delivered by #243

`Worker.forwarding` composes `delivery.notifications.Forwarding`; the same owned
worker selects new Inbox facts before its normal exact-attempt Discord dispatch.
Eight bindings and 100 notices each rotate per pass independently of credentials.
No run, conversation or recursive failure notification is created. Empty automatic
passes do not append audit rows. Read/unread and delivery receipts remain independent.

`Forwarding.configure(identity, Destination, kinds=[...], enabled=...,
expected_revision=..., operator_url=None)` is a trusted operator-only composition
seam, never a model tool. #247 must place it behind operator authentication.
Supported kinds are exactly `run.succeeded`, `run.failed`, `run.cancelled`; an empty
list forwards nothing. Connection plus guild/channel IDs and the optional bare
HTTP(S) operator origin are stored per binding. Destination IDs are immutable; URL
changes record a new revision and retain old origin pins for historical intent
validation. The URL forbids credentials,
paths, queries and fragments; it names the operator home alongside a truthful run
resource reference, without claiming a particular run is in the recent UI window.

Each configuration revision establishes a new current audit watermark and refuses
queued prior-revision deliveries transactionally. Dispatching/unknown/confirmed
operations retain original pins and evidence. Automatic selection never picks up
notices from a disabled interval or earlier filter. Deliberate backfill uses
`enqueue(identity, backfill_after=..., through_cursor=..., limit=...)`: both bounds
are explicit, at most 100 source notices are examined, and source/binding dedup
prevents resending any prior operation. It never moves the automatic cursor.

`inspect(after="", limit=100)` returns non-secret configurations, revision,
activation watermark, cursor and filters using keyset paging. `Delivery.inspect`
and `detail` expose operation state, exact attempts, eligibility and reason evidence
for #247's separate Notification deliveries view. Missing credentials leave queued
intent visible; connection revocation is swept without credentials. Common transport
permissions, full rate deadlines and honest unknown-send recovery remain authoritative.

[ADR 0022](adr/0022-notification-forwarding-selection.md) records schema 19's nullable
origin fill and legacy immutable payload preservation. Synthetic SQLite/loopback
checks do not select a live notification or complete #244's real acceptance.
