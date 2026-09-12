# ADR 0019: Scoped external communications with durable delivery evidence

Status: accepted, 2026-09-11. Architecture slice #240 of epic #239.

## Context

Herald is to read selected Discord channels, answer human mentions and publish
operator/routine announcements. Hearth currently has durable operator notices and
an unused `Forwarder` protocol, exact-run management tools, transactional task
admission and held restore, but no communications worker. Conversation bodies must
not become notification payloads. SQLite cannot commit atomically with Discord.
ADR 0018 already concerns sandbox recovery and is not a communications decision.

## Decision

Adopt [the communications contract](../communications.md). `channels/` owns
connections/routes, conversation turns and origin scope, durable cursors/dedup and
outbound operations/attempts. `channels/discord/` is a bounded REST transport;
`integrations/` remains AI providers. Work owns task creation and execution owns
admission and run accounting. There is no plugin framework or new approval system.

Conversation replies, resident announcements and operator notification deliveries
share delivery ownership/recovery while retaining separate payloads and provenance.
The dedicated Herald bot routes selected guild channels unambiguously. Explicit
read, mention/reply and publication grants are installation authority, never skill
text or bundle content. `guild_channel_humans` accepts verified human bot mentions
without granting Hearth operator status; explicit-operator routes remain available.
Conversation-origin runs are confined to source-channel history/replies and cannot
exercise announcement, resident-management or indirect delegation authority.

Tools check exact-run authority; the communications process rechecks current
route/grant authority at a durable dispatch permit. The writer serializes that
permit with revocation. Once permitted, a request may escape even after revocation;
cancellation cannot retract an external effect. HTTP stays outside writer
transactions and outside resident sandboxes. Lost acknowledgements and interrupted
dispatch permits remain unknown until evidence or explicit operator resolution;
worker locks and expired transport nonce windows never justify blind resend.

`channels/` defines schema invariants while `storage/` owns schema/version upgrades
and backup validation. Each implementing schema change bumps the current version
and supplies migration fills/maps/rewrites; existing stores receive empty disabled
scope. Text retention is bounded separately from durable dedup/cursor and delivery
evidence. Current backups preserve non-secret configuration and evidence; held
copies never poll, send or admit, and cannot take over a connection automatically.

## Consequences

This extends the project's bounded read-only source scope to inbound external
requests and explicitly granted publication. It leaves Letters and Notifications
with their existing meanings and keeps runtime and communications outcomes distinct.
Backend Discord egress is required; sandbox access is unchanged. Material changes
to deployment egress enforcement require #183 and measured host evidence.

A crash between dispatch permit and acknowledgement can require operator
reconciliation even when no message was sent. Unknown replies keep their
conversation busy; other routes can proceed. Metadata outlives transcript text,
and ordinary task/run evidence is not covered by a global transcript-erasure promise.
Cross-host ownership of a copied bot secret requires an explicit stopped-consumer
handoff; a local lock cannot enforce it remotely.

#137/#120 implement the shared owners, #241 composes the worker, #139 supplies
Discord, and #242/#243/#247/#244 complete tools, forwarding, UI and acceptance.
#140 retains Telegram-specific setup and real acceptance. This decision connects
nothing, changes no schema and completes no real-host/daily or #233 rollout gate.
