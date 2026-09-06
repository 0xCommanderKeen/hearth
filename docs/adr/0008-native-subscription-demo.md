# Native subscription execution for the Mac demo

Status: implemented on the real-Codex task branch, 2026-09-06.

The user explicitly selected real Codex execution and reuse of their existing
ChatGPT subscription login after accepting the mock demo checkpoint. Synthetic
notes, exact `gpt-6-astra`, this Mac, and $10/day API-equivalent accounting remain.

The first real adapter runs pinned native Codex CLI 0.153.4 with a separate
CODEX_HOME containing a private copy of the selected login. It loads no user or
project rules/configuration, disables shell, code-mode host, plugins, apps, hooks,
browser/computer and collaboration features, and uses a permission profile denying
filesystem access except the minimal runtime and denying tool network access.
The trusted CLI can contact its model/auth service; generated tools receive no
source grants. This is a native Mac demo boundary, not the offline container
fixture or proof of production container isolation. Existing global Codex files
are not modified. Auth remains outside Hearth data and backups.

A detached trusted worker owns an unreaped CLI process, an exclusive start claim,
input binding and terminal receipt. Server restart observes the same worker;
unknown worker/launch outcomes never authorize a relaunch. Cancellation signals
only the worker's own child process group. Local termination is not a claim of
provider-side cancellation or zero usage.

CLI 0.153.4 reports cache-write, cached-input, input, output and reasoning totals.
For total input at or below 272,000 tokens, all constituent requests necessarily
fall below the long-context threshold, so the linear API-equivalent calculation
on complete totals equals the sum of individual request prices. Missing counters
or larger aggregates leave usage unknown and preserve the admission hold. The
standard service mode is selected; estimates are not subscription charges or a
provider-enforced dollar ceiling.

Real execution is explicitly distinguished in API snapshots, artifacts, audit,
usage provenance, backups and browser labels. Mock publication and notifications
remain local mocks. Fresh stores are required; no historical conversion is added.

Verified first real standalone summary: fictional facts and uncertainty preserved,
malicious note instruction ignored, 43,482 microdollars API-equivalent. First
application trial exposed an informational pre-turn Code Mode diagnostic; a
regression now handles that exact disabled-feature diagnostic without treating it
as a model turn or masking other errors. The browser-to-resident-to-real-result
journey passed after that fix. Ongoing real daily usefulness and production
recovery gates remain unproven.
