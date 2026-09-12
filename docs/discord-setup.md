# Discord setup and acceptance

Hearth uses one dedicated Discord bot for one selected resident across explicitly
selected guild text channels. Start with a dedicated test channel and synthetic
messages. The setup controls are in Townhall → Communications → Settings; Letters
remain resident-to-resident work and Inbox notices remain durable local records.

This recipe and the credential-free journey implement #244 of #239. They do not
select a live account or complete real acceptance. Before connecting Discord, record
with the operator the bot, guild, selected channels, sender policy, protected token
directory, deployment host, resident ID, runtime/model and spending allowance. None
of those actual identifiers, contents or credentials belong in this repository.
The #233 deployment and daily-operation gates remain open.

## Install a narrowly scoped bot

Create a dedicated application in the Discord Developer Portal. Configure its bot,
choose Guild Install and install it into the selected test server using the `bot`
scope. This REST polling integration needs no slash commands, Gateway connection or
public interactions endpoint. Record the bot user's numeric ID and the selected
guild/channel IDs; display names do not establish authority. Generate the bot token
on the Bot page and place it directly in the protected file described below.
[Discord application and installation guide](https://docs.discord.com/developers/quick-start/getting-started).

Grant View Channel, Read Message History and Send Messages only where needed. Their
combined permission integer is 68608; do not request Administrator. Channel and
category overrides must permit the selected channel while excluding unintended
channels. A read-only channel does not require Send Messages; a mention route needs
history and reply access. Notification forwarding also needs send access at its
own destination. The operator installing the bot needs permission to manage the
server. Hearth rechecks effective roles and channel overrides before requests.
[Discord permissions](https://docs.discord.com/developers/topics/permissions).

Enable Message Content Intent in the application's Bot settings for general channel
history; obtain Discord approval if required for that application. REST is subject
to the content restriction too. A successful bot mention does not demonstrate
access to unmentioned messages, and an empty result does not demonstrate complete
history. Hearth checks application flags and refuses a general history tool result
when content access is unavailable or unknown.
[Discord HTTP content restrictions](https://docs.discord.com/developers/events/gateway#http-restrictions).

## Keep credentials in the backend

Choose an absolute directory outside the checkout, the Hearth store, artifacts,
backups, resident folders and runtime login directories. It must belong to the
backend's UID with mode 0700; each token slot is a regular, single-link owner-only
file with mode 0600. Symlinks, group/world access and oversized files are refused.
The slot name is lowercase letters/digits/underscores beginning with a letter,
for example `herald_bot`. The file contains only the token; the UI/CLI takes only
this slot reference and never asks for the token. Use a protected editor or secret
manager to write the file. Do not pass the token as a shell argument or put it in
`.env`, a declaration, skill, memory, bundle or source note.

For a native backend, set `HEARTH_COMMUNICATIONS_SECRETS` to that directory before
starting `hearth.app:from_env`. Leaving it unset leaves communications without a
credential resolver. Configuration can still be saved pending. On a new install,
first provision the resident through Townhall, choose its runtime and allowance,
and complete the deployment's runtime setup; no pre-existing data directory is
required. Installing the [Herald etiquette](skills/herald-etiquette.md) grants no
communications authority.

For the production container, the opt-in override mounts the selected directory
read-only at its identical host path, only into the backend. Pre-create it and set
ownership to the configured `HEARTH_UID`; the override refuses a missing host path.
Add the non-secret absolute path to your private deployment environment file:

```sh
docker compose --env-file /private/path/hearth.env -f deploy/compose.yaml \
  -f deploy/compose.communications.yaml up -d
```

Apply the override consistently on later starts/upgrades. The existing protected
mount checks refuse the directory to resident runs at grant, admission and launch.
It is absent from backups and is not a mount in the sandbox image. Rotating/removing
a token is an operator file operation; the worker reloads it and invalidates its
cached client. A copied secret on another machine remains outside local fencing.

The backend needs DNS and TLS egress to `discord.com:443`, fixed REST origin
`https://discord.com/api/v10`. Its egress is distinct from the AI sandbox network
and the fence in [the deployment runbook](../deploy/README.md). Verify both on the
selected host; do not widen sandbox mounts or network permissions for Discord.
The synthetic Docker check below runs with `--network none` and proves only local
wiring. It does not prove the intended host's Discord egress or sandbox isolation.

## Save, inspect and activate the selected scope

In the shared Settings page, save the bot connection with transport Discord, its
bot ID, label and non-secret slot reference. Then select that connection and the
actual resident (its effective runtime is displayed), enter the guild/channel IDs,
and save a dedicated route with `guild_channel_humans`. This admits any human
member's verified mention in that selected channel; it does not add them to Hearth's
operator allowlist. `operators_only` remains a separate explicit sender-ID policy.

Read history, Listen for mentions, Reply and Post announcements are four independent
grants. A read alone starts no work; mentions require both listen and reply. Give
publication only to intended destinations. Repeat the bounded channel setup for
additional selected destinations. Saving one destination preserves the resident's
other grants. A binding's bot identity or route address/resident cannot be edited
into another identity: disable it and explicitly create a new binding.

Save enabled configuration deliberately, then activate installation ownership only
after stopping every old consumer of the same bot credential. The activation
checkbox is an operator assertion about other consumers; Hearth cannot verify
another machine. Pending is configuration without effective execution; an enabled
connection without a token is stored pending. Grant/route saves and activation are
separate revisioned operations, with no transactional promise across the form steps.
A conflict requires reload and review, never overwriting somebody else's revision.
Activation may start polling immediately; keep the test channel quiet until its
initial baseline has been observed. Old history remains readable but is never
backfilled as mention tasks.

Click the explicit probe to verify the selected route's current bot identity,
permissions and content access. The worker must be running, the connection/route
enabled, a grant selected and installation ownership activated. Probe does not
read messages, send, admit work or establish the polling baseline. Reloading the
page reads stored/cached evidence only. Do not treat the probe as an activation
precondition that prevents polling after activation.

The CLI uses the same authenticated server and owner. Supply
`HEARTH_OPERATOR_TOKEN` in its environment and use `--url` for the bare operator
origin. These JSON files hold non-secret configuration values only, stored outside
the repository for a real installation:

```sh
hearth communications save --kind connection --id herald_bot \
  --expected-revision 0 --file /private/setup/connection.json
hearth communications save --kind route --id test_room \
  --expected-revision 0 --file /private/setup/route.json
hearth communications save --kind grant --id herald \
  --expected-revision 0 --file /private/setup/grant.json
hearth communications list --section configuration
hearth communications activate --id herald_bot --expected-revision 0 --old-consumer-stopped
hearth communications probe --id herald_bot --route test_room
```

For illustration only, these numeric IDs are synthetic and must be replaced with
the operator's selected values. Set the real resident ID explicitly:

```json
{"transport":"discord","bot_id":"300","secret_ref":"herald_bot","state":"active","label":"Herald"}
```

```json
{"connection_id":"herald_bot","resident_id":"herald","address":{"guild_id":"100","channel_id":"200"},"sender_policy":"guild_channel_humans","state":"active","mode":"dedicated"}
```

```json
{"read":[{"connection_id":"herald_bot","guild_id":"100","channel_id":"200"}],"listen":[{"connection_id":"herald_bot","guild_id":"100","channel_id":"200"}],"reply":[{"connection_id":"herald_bot","guild_id":"100","channel_id":"200"}],"post":[]}
```

`save` replaces the full selected value and requires the current configuration
revision (zero only for creation). It does not merge grants. `activate` uses the
separate installation binding revision, zero only for the first handoff. After a
lost response, list/reload first and compare the saved configuration or binding;
blindly increasing the expected revision is not recovery.

Revoke a route, connection or all of a resident's communications grants through the
shared UI or CLI, using the listed configuration revision:

```sh
hearth communications revoke --kind route --id test_room --expected-revision 1
hearth communications list --section configuration
```

Revocation refuses undispatched invalid operations in the owning transaction and
stops later dispatch permits. A request already permitted may still reach Discord;
confirmed and unknown effects remain visible. Unknown has no ordinary Retry.
Inspect exact attempts and resolve only with matching external evidence, or abandon
while preserving the unknown outcome. Never delete a cursor to make work start again.

## Interpret evidence

| Observation | Meaning and next step |
| --- | --- |
| Pending / credentials unknown | No successful credential check is recorded for this revision; do not infer token presence. |
| Credential missing | The worker's timestamped protected-file lookup found no slot. Seed it and explicitly enable pending configuration. |
| Credential invalid | File ownership/mode/type/reference validation failed; fix the protected file. No content is returned. |
| Credential configured | The protected file was readable at the recorded time; this is not proof Discord accepted the token. |
| Authentication failed | Discord returned an authentication rejection; replace the token securely and respect the cooldown. |
| Permission denied | Verified effective access or the REST response refused the requested channel operation. Inspect roles/overrides. |
| Content unavailable/unknown | General history is incomplete/refused even when mention replies work; inspect Message Content access. |
| Rate limited | Wait until the stored eligible time. Full server bucket/global delays apply, even beyond 30 seconds. |
| Poll progress | Durable successful baseline/scan/progress time and watermark. An open window is still in progress; it is not a live heartbeat. |
| Disabled/revoked | Current authority is off. Past sent or unknown effects retain their independent outcomes. |

REST polling can leave the bot showing offline in Discord. The normal worker ticks
about every half second, but polling backlogs, rate limits, network deadlines,
resident availability and runtime execution add latency. No presence indicator is
proof of failure or readiness. Discord's prescribed waits govern retries; lost
send acknowledgements remain unknown and never automatically resend.
[Discord rate limits](https://docs.discord.com/developers/topics/rate-limits).

## Synthetic and real verification

Run the entire contributor gate with `make check`. Its clean installed-wheel
journey injects a test-only fake native runtime and a loopback Discord REST fixture;
neither implementation is packaged. Run the same fixture inside the actual
production Docker image using `scripts/check-discord-docker.sh`. The fixture has
synthetic protected secrets, real temporary SQLite and no live Discord/runtime
credentials. The Docker run mounts test code read-only and has no external network.
See [the recorded evidence](evidence/discord-setup-2026-09-11/README.md) for exact
checks, source identity, browser observations and limitations.

The real gate remains unchecked until the selections at the top of this page are
made. On that selected host independently verify in both Discord and Hearth:
bounded general history, an operator/routine announcement, a non-operator human's
mention/reply, and a separately forwarded run notice. Check task/run/origin, known
or unknown cost, exact outbound receipt, permissions and revocation. Record only
sanitized outcomes and aggregate costs in the repository. A useful real response,
actual subscription use, host recovery and daily observation cannot be established
by the synthetic fixtures.
