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
