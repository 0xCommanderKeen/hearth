# Discord setup: synthetic acceptance, 2026-09-11

Slice #244 of epic #239, stacked on `epic/239-247` at
`7717a6a4cdf9b6d734eecccfacf4cf28d24e253b`. All identifiers, messages, credentials
and runtime output here are synthetic. No live Discord account, provider login,
actual model call or intended deployment was selected or accessed.

## Owning interfaces and installation

The shared fixture is `tests/discord_journey.py`; `scripts/smoke-communications.py`
asserts installed-package provenance and invokes it from an unrelated temporary
directory. `make check` builds and installs the production wheel with locked runtime
dependencies and runs that smoke with Python isolated mode. The fixture injects
only the existing private Discord loopback network seam and the test-only
`FakeNativeRuntime` subclass. The actual REST adapter, native tool Bridge,
Configuration, Worker, admission, runtime settlement, forwarding and backup owners
operate over real temporary SQLite. Fixture HTTP handlers obtain a SQLite writer
to check that network I/O is outside operational transactions.

The journey proves:

- Selected bounded history with message provenance; unselected scope and unavailable
  content refuse. Discord permission loss is visible and starts no additional work.
- Operator and routine announcements have distinct task/run origins and confirmed
  receipts. Revoking a queued publication grant refuses the intent transactionally
  before it can send, even if the grant is later restored.
- More than one page of messages produces one authorized human mention task/reply;
  unmentioned, bot, webhook, system, unselected-channel and denied private-sender
  cases start no additional task. Source-origin publication refuses. Outbound
  messages re-enter the fixture as bot-authored history and start no reply loop.
- A 37-second global rate limit prevents early network calls. Lost acknowledgement
  produces unknown delivery; further passes and restart do not resend or re-admit.
  Three runs have known positive API-equivalent costs; one has unknown cost retained
  as null, separately from the unknown external delivery.
- One selected run notice forwards independently, with source run/cost linkage and
  its existing Inbox read state. Repeated bounded backfill cannot duplicate it.
- A populated schema-18 layout upgrades through the normal owner to schema 19 and
  retains its original file beside it. This fixture derives that old layout by
  removing the then-absent empty origin table/column before upgrade; it imports no
  external store. Conversations, current/historical configuration, native requests,
  calls and pins, notices, cursors, operations and attempts are compared explicitly.
- Current backup/held restore preserves the same evidence, including unknown sends;
  held worker entry, step, probe and activation refuse before network or lock creation.

The run has four runs, two conversation turns, six operations (including one
refused and one unknown), and five actual fixture POSTs. The fixed sequence makes
810 bounded loopback requests, usually finishing in about three seconds. It does
not call a model. Synthetic known cost tests accounting plumbing and is never a
provider charge or evidence of model quality.

The real-HTTP CLI test starts authenticated Uvicorn over a temporary store and
executes the installed command shape to save connection/route/grant, reload, reject
stale revisions, seed a synthetic protected slot, enable, explicitly activate and
revoke. API checks cover held refusal, cached-only credential diagnostics and
production `from_env` protected-root wiring. Actual management grants for the secret
root and parent refuse; noncanonical/symlink roots refuse application startup.
An independent review reproduced and then verified fixes for both a future-revision
revoke race and the symlink-root protection gap.

## Browser evidence

Chromium 152.0.7977.82 on Arch Linux, headless via Playwright 1.62.0; Python 3.14.7,
Node 26.7.0, pnpm 11.22.0. Desktop and 390-pixel mobile views were checked. The
three bounded driver scripts were run from temporary files using:

```sh
mise exec node@26.7.0 pnpm@11.22.0 -- pnpm --dir web exec vite --host 127.0.0.1 --port 5179
mise exec uv@0.12.12 -- uv run --frozen --with playwright python /tmp/hearth-244-setup-browser.py
mise exec uv@0.12.12 -- uv run --frozen --with playwright python /tmp/hearth-244-shared-forms-browser.py
mise exec uv@0.12.12 -- uv run --frozen --with playwright python /tmp/hearth-244-navigation-browser.py
```

- [Setup API result](setup-api-result.json): browser requests forwarded verbatim to
  authenticated FastAPI TestClient and real temporary SQLite. Selected resident/runtime,
  dedicated route, separate grants, activation acknowledgement, keyboard save,
  revision conflict/reload, revocation and real held backup restore pass. Workers
  are stopped; no external probe or background polling is claimed. The synthetic
  protected token is absent from the DOM. All held write controls are disabled.
- [Shared form API result](forms-api-result.json): the same real API/SQLite boundary
  passes 18 create/update writes across all three transports and both forwarding
  destination shapes, both chat route shapes and all four grant lists. Reload preserves immutable
  fields and the ntfy target shape. There is no external worker or transport call.
- [Navigation result](result.json): synthetic mocked API JSON checks successful-run
  versus unknown-delivery display, exact run navigation, Letters, keyboard resolution,
  conflict/reload and no render probe. This is browser interaction evidence, not a
  real delivery/database journey.

All three report zero page errors and no mobile horizontal overflow. Screenshots:
[desktop setup](desktop-discord-setup.png), [mobile revoked route](mobile-discord-revoked.png),
[held controls](mobile-held-discord.png), [desktop unknown](desktop-unknown.png),
[mobile unknown](mobile-unknown.png), [resident deep link](resident-deep-link.png).
The ephemeral browser drivers are not packaged; maintained focused React tests and
owning API/CLI tests cover these forms in the contributor gate. Native accessibility,
physical phone GPUs and the intended host remain unverified.

## Final verification

The frozen implementation is verified with:

```sh
mise exec uv@0.12.12 pnpm@11.22.0 -- make check
scripts/check-discord-docker.sh
```

The Docker script builds the actual `deploy/Dockerfile` production image and executes
its installed wheel as the image's non-root user with a read-only root, dropped
capabilities, no-new-privileges, temporary data and `--network none`. Tests/scripts
are read-only fixture mounts, never image layers; no daemon socket, account directory
or real token is mounted. The adapter's loopback endpoint is inside that container.
This proves production-image wiring on the available Docker host, not deployment
or live runtime isolation on the intended host.

The final reviewed-tree [Docker journey](docker-result.json) passed on Docker
29.7.2, image `sha256:c5e8bb9ae4e11136f5c329c72fab361515e5e7e3270259eef9765598619c66e5`.
It recorded 2,100 microdollars of synthetic known API-equivalent cost across three
runs, and one run with unknown cost. The full [contributor gate](check-result.json) passed: 1,392 backend tests,
234 frontend tests in 33 files, lint/format/types, production build, packaged
assets and both clean installed-wheel journeys. The [source inventory](source-inventory.json)
includes all tracked and nonignored untracked files except evidence metadata.
Its aggregate SHA256 is `7b46d3dcd8ea017157f881f1e8dfc7fc7f74960711c5c90ed5e87c28d933966d`;
the mapping was verified unchanged after both final commands. The optional Compose override
already passes `docker compose config --quiet` using only synthetic environment
values and the checked-in non-secret example.

## Remaining real gate

**Unchecked:** the operator must first select the bot, guild/channels, sender policy,
protected token location, host, resident/runtime/model and allowance. A real bounded
history read, announcement, human mention/reply and separate run notice must then
be independently observed in Discord and Hearth, including permission loss and
revocation. Unknown runtime usage must remain unknown.

The #233 rollout order, real recovery, daily observation and intended-host gates
remain open. This PR is related to #244 and does not close its unfinished real gate.
