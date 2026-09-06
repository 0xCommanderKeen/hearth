# Local Codex CLI simulation

This demo runs the actual pinned Codex CLI, using generated synthetic auth and a
local canned model service inside offline containers. No subscription login, API
key or real model call is involved. Use a fresh data directory.

With Docker available and the verified Linux arm64 CLI archive downloaded as
described in [the probe instructions](codex-subscription-probe.md):

```sh
HEARTH_DATA=/private/tmp/my-hearth-demo \
HEARTH_OPERATOR_TOKEN=choose-a-local-demo-password \
HEARTH_MOCK_RUNTIME=codex_mock \
HEARTH_CODEX_ARCHIVE=/absolute/path/codex-linux-arm64.tgz \
uv run uvicorn hearth.api:from_env --factory --host 127.0.0.1 --port 8769
```

Open the local URL, enter that operator token (the local control password), and
set up Reader. Open Residents → Reader, assign a mock summary and open its result.
The $10/day limit uses API-equivalent token estimates; this simulation spends no
money. Only synthetic notes are provided. Normal runs have a bounded lifetime.

If the process or daemon fails, reopen the same data directory with the same
runtime selection. Hearth observes existing run identities; it does not relaunch
a possibly started task. Unknown outcomes and incomplete usage remain visible.
Do not delete runtime files to force retries. A held backup is read-only.

The adapter is a mock development option. Actual subscription authentication and
real-model acceptance remain separate, explicitly selected work.
