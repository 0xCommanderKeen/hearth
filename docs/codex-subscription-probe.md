# Offline subscription CLI probe

The selected authentication is Codex/ChatGPT subscription, with no API-key fallback.
This opt-in probe runs the actual Linux arm64 CLI 0.145.0 on the development Mac's
Docker Desktop VM. All authentication, model metadata, notes, responses and token
usage are synthetic. It neither establishes account/model access nor consumes a
subscription. The application still runs mocks only.

Download the pinned public npm archive to a temporary path, then run from the
checkout with its development environment:

```sh
curl --fail --location https://registry.npmjs.org/@openai/codex/-/codex-0.145.0-linux-arm64.tgz -o /tmp/hearth-codex.tgz
uv run python scripts/probe-codex-subscription.py --archive /tmp/hearth-codex.tgz --report /tmp/hearth-codex-report.json
```

The driver verifies SHA-512 before extracting the exact bytes; it runs no package
installer. It requires the already-cached digest-pinned image from the
[Mac container probe](mac-isolation.md). There is no implicit image pull, login or
personal configuration read. Only the verified CLI bundle and fixture script are
mounted, readonly; fresh synthetic state lives on bounded scratch. The container
has no external network, root write access, Docker socket or Hearth data mount.
Exact inspected ownership guards cleanup. A failed observation refuses a passing
report; if ownership cannot be verified, cleanup refuses too.

The fixture accepts only a bounded WebSocket request at `/responses` and bounded
local analytics posts. It has no proxy/forwarding code. The pinned CLI sends an
optional warm-up request with `generate: false`; that is recorded separately from
the generated response. It also sends analytics despite disabling the OTel metrics
exporter; those are consumed locally, never forwarded. CLI output is captured on
the limited scratch filesystem, then read with a one-MiB limit per file. Deadlines
bound both the CLI and host attachment.

Two cases verify a fixed completion and injected tool calls. Both require actual
CLI exit zero, exact final-file/parsed-output agreement, `gpt-6-astra`, synthetic
Bearer/account headers and no server errors. The injection case requires explicit
rejection of `exec_command` and of `view_image` for the text-only model profile;
the shell marker must remain absent. Plan/input/image tools still appear in the
request, so this is not described as a no-tools interface. Image rejection here
is a modality check, not evidence of filesystem permission enforcement.

Exploration found that `chatgpt_base_url` alone did not redirect inference. With
`openai_base_url` set to the loopback fixture, the built-in provider retained
ChatGPT authentication and used local WebSockets. No custom API provider or API
key is needed. The published `tools.view_image` setting was rejected by this
pinned CLI's strict configuration; this probe records observed behavior rather
than assuming all current documentation applies to that binary. See the
[sourced subscription research](codex-subscription-research.md).

The report records exact archive, executable, script and image identities, CLI
events, final output and sanitized transport metadata. It contains only synthetic
data. CLI token counters do not establish subscription dollars or a $10 provider
cap. Real credential separation, failure/retry/cancellation behavior, authoritative
run receipts and real subscription usage remain open before application wiring.
