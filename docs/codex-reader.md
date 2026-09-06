# Bounded Codex Astra Reader integration

Checked official OpenAI documentation on 2026-09-06. Documentation-only research: no model calls, authentication commands, credential/config inspection, or execution of Codex tasks. Hearth remains mocks-only. User selected Codex Astra, synthetic notes, this Mac for development and $10 per day. Fresh Reader setup uses Europe/Ljubljana budget days. Real testing remains deferred.

Mock integration now pins adapter kind, contract version and exact input digest
at admission. Fresh stores can select the process mock, which participates in
quiescent backup and held restore. These pins do not yet describe a real model or
pricing. The remaining sequence below is the design for actual Codex execution.

## Established interface

The selected model identifier is `gpt-6-astra`. Official model documentation lists reasoning efforts low, medium, high, xhigh and max. Its API model page establishes the identifier and advertised capabilities, but does not prove access through the eventual CLI account. Preserve the selection and fail visibly if unavailable; do not silently substitute a model. [Model guidance](https://developers.openai.com/api/docs/guides/latest-model), [Astra model](https://developers.openai.com/api/docs/models/gpt-6-astra).

`codex exec --json` emits JSONL, including thread/turn lifecycle events, item events and errors. The documented `turn.completed` usage object contains `input_tokens`, `cached_input_tokens`, `output_tokens`, and `reasoning_output_tokens`; the example has no dollar-cost field. Agent message text appears under an `item.completed` event's `item`. `--ephemeral` disables persistent session rollout files. Saved sessions can be continued by ID with `exec resume`; that is a continuation mechanism, not documented idempotent task submission. [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).

The CLI reference documents model selection, stdin prompt (`-`), explicit working directory, JSON output, output file/schema, outside-repository execution and explicit sandbox settings. `--ignore-user-config` skips the user config file but authentication still uses `CODEX_HOME`; `--ignore-rules` skips user/project execpolicy rules. `--strict-config` rejects unrecognized config fields. These switches are individual controls, not a documented complete isolation profile. [CLI commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

Parent agent separately observed local CLI version 0.145.0 and matching help flags without launching a task. This is local interface evidence, not tested runtime behavior or account availability.

## Isolation and authentication

Official guidance documents read-only plus approval policy `never` for noninteractive read-only work. Enforcement differs by OS: macOS Seatbelt and Linux bwrap/seccomp. Linux container restrictions may prevent the sandbox from working. This does not establish that an arbitrary host has the isolation Hearth requires. [Approvals and security](https://learn.chatgpt.com/docs/agent-approvals-security).

Codex's sandbox constrains spawned commands and filesystem operations. **Inference:** a read-only mode alone cannot prove that only Reader's notes are readable or that the parent CLI lacks access to Hearth's database, host credentials and other residents. A separate execution boundary must restrict what is available to the process, then be tested on the selected host. [Sandbox](https://learn.chatgpt.com/docs/sandboxing).

Codex state lives under `CODEX_HOME`; project config is also discovered, trusted project layers can load, and user/system layers remain separate. Hooks can load independently. Setting `project_root_markers = []` stops parent-directory project discovery. **Design implication:** use a fresh execution workspace and isolated state directory, explicitly controlled configuration/environment, and audited tool availability. Merely ignoring the main user config is insufficient evidence that nothing else loads. [Advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced).

Authentication can use ChatGPT subscription access or API usage billing. Credential storage can be a file under `CODEX_HOME`, the OS credential store, or automatic selection. API authentication uses standard API rates. **Decision still needed:** select the dedicated authentication/billing method before creating a credential or using an existing account. No host login should be inherited implicitly. [Authentication](https://learn.chatgpt.com/docs/auth).

## Accounting and recovery limits

Configuration documentation describes rollout-budget token tracking as under development and disabled by default; it is not a documented hard dollar cap. The searched CLI/configuration references did not establish a hard USD limit, remote cancellation receipt, restart-safe process ownership, or idempotency key for `exec`. Absence from these pages is a documentation gap, not proof that no other interface provides them. [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

**Hearth design implications:** retain a durable launch intent and worker receipt before accepting completion, bind captured output to the admitted run, and never treat missing terminal evidence or a dead CLI PID as proof of remote cancellation or zero cost. A local process timeout alone cannot establish a $10 provider spending ceiling. Do not equate session resume with safe retry after uncertain dispatch. These are integration requirements, not claims of provider guarantees.

## Hearth fit and smallest implementation sequence

These are design proposals, derived from the current source rather than claims
about implemented behavior. Keep one Codex adapter for one Reader on one burrow.
Do not introduce a runtime marketplace, general scheduler or migration layer.

1. **Make execution provenance explicit.** The current `Runtime` protocol exposes
   start/inspect/stop and `Evidence(status, output, cost)` only. Simulation is fixed
   in `run_context.py`, `execution.py`, `artifacts.py`, `schema.py`, `observation.py`,
   `backup.py`, API responses and the browser snapshot validator. Before real
   wiring, carry immutable runtime/model/version, input digest, usage provenance
   and simulation status from admission through artifact, audit, snapshot and backup.
   Distinguish synthetic source material from simulated execution: a real model
   summarizing fictional notes still incurs real usage. Keep mock effects and
   notifications visibly simulated. Define the new current schema directly and
   use a new data directory; do not add historical upgrades.
2. **Prove a durable worker boundary with a fake executable.** `Executor.step()`
   currently holds its operation lock while calling `start`; the real adapter must
   return promptly so the supervisor can observe cancellation. A trusted worker
   owns the Codex process and persists a launch claim before spawning, then bounded
   event/output evidence and terminal observation. A repeated start with the same
   run ID/input must attach to existing evidence; a different input must refuse.
   Missing or ambiguous evidence after a launch claim means unknown, never permission
   to spawn again. Use a verifiable process identity, not a bare reusable PID.
   Prove interrupted launch, engine restart, worker crash, descendant cancellation,
   duplicate start and late output against temporary SQLite and a credential-free
   fake process before connecting Codex. Do not treat resume as an exactly-once
   launch primitive. Resolve the process/container ownership mechanism for the
   selected host before implementation.
3. **Parse a pinned Codex event contract.** Bound individual events, total output,
   elapsed time and retained files. Accept a successful result only with the expected
   completion evidence and observed worker termination. Preserve raw token counts
   and the exact pricing/accounting basis separately from estimated microdollars.
   Missing usage, malformed terminal events or unknown termination retain the
   existing hold. A process exit alone does not prove a successful summary or zero
   cost. Test this parser with documented synthetic event fixtures, including
   interrupted and malformed streams; fixture success is not real compatibility.
4. **Stage only Reader's pinned inputs.** Materialize purpose, skill, task, memory
   and synthetic notes from the admitted context into an isolated per-run directory.
   Do not mount Hearth's database, host home, other residents, operator token,
   approval credentials or engine socket. Treat notes as data, not permission.
   The first summary should have no external effects or source connectors. Keep
   the trusted launch/evidence worker outside the model's writable filesystem.
5. **Verify on the selected burrow, then enable one bounded real test explicitly.**
   With a credential-free probe, demonstrate denied reads/writes outside staged
   inputs, denied arbitrary network access, denied access to control/credential
   paths, and reliable descendant termination. Permit only the model connection
   needed by the trusted runtime, with credentials inaccessible to generated tools.
   Then pin the installed CLI, model availability, authentication/billing mode,
   pricing, allowance window and stop policy. Explicit real-test selection remains
   required; neither the model choice nor passing mocks enables paid execution.

For the first real output, use a small fictional notes fixture with known facts:
completed work, unresolved decisions, dates, owners and next actions. Check that
its summary cites the supplied notes, preserves uncertainty, adds no invented
facts, and follows the requested daily-summary format. Include a note that asks
for an unrelated action to verify it remains source text. A deterministic fixture
cannot establish model quality or the host isolation boundary.

## Decisions still needed

- Mac process and isolation mechanism, verified with credential-free probes before
  connecting the model. Development on this Mac is selected; no remote deployment
  is selected.
- Applicable billing and stop policy for the confirmed $10/day allowance. Hearth
  admission/accounting is not a provider-enforced ceiling; delayed token telemetry
  cannot establish an exact billed-cost cap.
- Account/authentication mode and accessible exact Astra model on that burrow,
  verified without exposing existing personal credentials. No fallback model.
- Explicit selection of a real test after mock worker checks and host evidence.

The application supports inline and process-backed mocks. This document is an
implementation design for real execution, not proof that a Codex adapter or
isolation exists.
