# Real Codex subscription demo on this Mac

Install the pinned official `@openai/codex@0.153.4` package into a private local
directory. Use its native Darwin arm64 binary, a separate CODEX_HOME and the
operator-selected ChatGPT login. Keep auth files private and outside the repo,
Hearth data and backups. Never supply an API key as a fallback.

```sh
HEARTH_DATA=/absolute/path/to/fresh-hearth-data \
HEARTH_OPERATOR_TOKEN=choose-a-local-control-password \
HEARTH_RUNTIME=codex_subscription \
HEARTH_CODEX_BINARY=/absolute/path/to/native/codex \
HEARTH_CODEX_AUTH_HOME=/absolute/path/to/private-codex-home \
uv run uvicorn hearth.api:from_env --factory --host 127.0.0.1 --port 8771
```

Open Townhall, set up Reader, then Residents → Reader → Run summary. Reader
receives synthetic notes and uses the real model. The result is saved as Markdown;
the displayed dollar amount is an API-equivalent estimate for subscription usage.
The default daily limit is $10 in Europe/Ljubljana time. Incomplete or unpriceable
usage keeps new admission paused. Existing mock effects/notifications stay local.

The local control password is unrelated to Codex authentication. A successful login
is remembered in this browser tab across refreshes; Lock or closing the tab clears
it. The saved token is revalidated by the server after refresh. Expired or
unavailable subscription access fails visibly; no model or billing fallback is
selected. See [the execution decision](adr/0008-native-subscription-demo.md) for
boundaries and remaining recovery limits.

Official references: [Codex authentication](https://learn.chatgpt.com/docs/auth),
[non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode),
and [configuration](https://learn.chatgpt.com/docs/config-file/config-reference).
