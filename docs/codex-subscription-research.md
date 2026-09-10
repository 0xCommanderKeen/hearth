# Codex subscription boundary

Official OpenAI documentation checked 2026-09-06 for issue #69. The selected route
is **Codex with ChatGPT subscription sign-in**, model `gpt-6-astra`, on the Mac,
with a $10/day operator limit. Execution was still mocked when this was written.
API-key billing is not a fallback. This research did not inspect account state, credentials or user config,
run Codex tasks, or contact a model service.

## Supported authentication controls

Codex CLI supports ChatGPT subscription sign-in. `forced_login_method = "chatgpt"`
restricts the authentication method; mismatched stored credentials cause logout
and exit. Cached credentials can live in `auth.json` under `CODEX_HOME`, the OS
keyring, or automatic selection. Consequently, a new state directory alone does
not establish isolation from the OS credential store. Use explicitly selected
file storage with entirely synthetic contents for offline probes. Do not apply
forced-login settings to the operator's existing state.
[Authentication](https://learn.chatgpt.com/docs/auth).

`chatgpt_base_url` is documented as overriding the base URL used during the
ChatGPT login flow. Project config cannot override it. This wording does **not**
establish that it redirects every inference, refresh, model-catalog or telemetry
request. `features.shell_tool` controls the default shell tool, and the reference
lists other tool features separately; switching off shell alone is not proof
that the run has no tools.
[Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

App-server has an experimental `chatgptAuthTokens` mode for a host that owns the
auth lifecycle. It takes an access token, account ID and optional plan type. On an authorization error, it can request refreshed
tokens from the host and retry after a successful response. This is an app-server
credential handoff, not a documented `codex exec` flag or a guarantee that tokens
are inaccessible to tools executing in the same security domain.
[App-server authentication](https://learn.chatgpt.com/docs/app-server#authentication).

Noninteractive documentation describes ChatGPT-managed automation separately
from API authentication and treats cached auth as a secret. Its headless cache
workflow is not intended for public/open-source CI. Hearth's public CI should
therefore continue using synthetic transport fixtures, with no subscription
credentials or real account workflow.
[Noninteractive automation](https://learn.chatgpt.com/docs/non-interactive-mode).

## Isolation and accounting limits

Permission profiles support denying the filesystem root while allowing minimal
runtime paths and selected workspace paths. Command networking can be disabled;
network domain restrictions require an active proxy. These controls constrain
tools but do not themselves prove isolation of the trusted CLI process and its
credentials from every generated capability.
[Permissions](https://learn.chatgpt.com/docs/permissions).

App-server reports ChatGPT rate-limit windows with usage percentages and reset
times, and optional plan/remaining workspace credit information. Those fields do
not establish a dollar cost for one Reader run or an enforceable $10/day ceiling.
Miha subsequently selected API-equivalent estimates for internal accounting:
reported tokens will use API prices under the same $10/day policy as a future API
backend. These estimates are distinct from subscription charges. Do not label
unknown usage as zero or silently buy credits. Rate-limit reset consumption is
outside this probe.
[App-server rate limits](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt).

## Smallest offline compatibility probe

This is a proposed Hearth test, not a provider guarantee:

1. Run the pinned CLI inside the already tested container boundary with fresh
   state, explicit synthetic file-based subscription auth, a scrubbed environment
   and synthetic notes. Provide no real token, API key, host auth mount or keyring.
2. Put a fake ChatGPT transport on loopback in the same network-disabled container.
   External networking must remain unavailable even if a URL override is ignored.
   Capture only synthetic request metadata; fail on unexpected endpoints/methods.
3. Preserve `gpt-6-astra` explicitly. Serve a fixed synthetic response and verify
   the CLI's observed request route, model, tools and completion output. An
   unsupported mock auth format or route is a compatibility failure, never a
   reason to try the operator's credentials or an API-billed provider.
4. Observe the actual offered tools rather than inferring their absence from a
   small flag list. Add a malicious synthetic tool-call response to prove that
   the configured probe cannot read an out-of-scope canary or contact a network
   endpoint. Keep transport compatibility distinct from full tool confinement.
5. Mark every output as coming from the fixture. Record the CLI version, effective
   configuration, fixture protocol and container evidence; test rejection, truncation
   and timeout.

This can establish compatibility with an offline fixture for that CLI version.
It cannot establish subscription availability, real auth refresh, actual model
behavior, provider cancellation or financial enforcement.

## Eventual credential separation

Keep subscription credentials in a trusted component outside generated-tool
execution. Either a verified broker owns upstream subscription transport, or a
trusted app-server owns auth while tools execute behind a separate enforceable
boundary. These are design options requiring implementation and probes, not
currently documented turnkey guarantees. App-server external-token support may
help the second option but does not remove the boundary requirement.

Before real use, prove token secrecy across files/environment/process inspection,
restrict the model channel against arbitrary forwarding and unbudgeted requests,
verify account/model access and refresh behavior, settle honest subscription
accounting for $10/day, and obtain explicit authorization for a bounded real test.
