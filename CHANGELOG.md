# Changelog

One line per merged PR, newest first. Decisions live in `docs/adr/`.

- Hearth is deployable on a Linux server as a container, and it will not open on a
  sandbox network it cannot see holding. `deploy/Dockerfile` packages the release wheel,
  the locked dependencies and a container client with no package manager and no root;
  `deploy/compose.yaml` is the deployment -- a store volume, a credentials volume, a
  folders volume, the pinned CLIs read-only, the runtime socket, `HEARTH_SANDBOX=container`
  and both image digests as environment. Every volume is mounted inside Hearth at the
  path it has on the host, because the daemon resolves the paths Hearth hands it in the
  host's filesystem and not in Hearth's; Hearth is deliberately not on `hearth-egress`,
  because a session reaches Hearth over a socket file and needs no network path to it.
  The fence itself is measured, never assumed: `HEARTH_SANDBOX_SHUT` and
  `HEARTH_SANDBOX_OPEN` name what a session must not and must be able to reach, and at every
  start -- and again on every `GET /api/health` -- Hearth runs one container on the
  sandbox network, from the pinned image, as its own uid, with nothing mounted, and asks
  it what it reached (`integrations/reach.py`). A reset counts as reachable, because a
  packet that arrived is not a fence. What it saw is recorded as `sandbox.fence` and
  anything but "the provider and nothing else" refuses `sandbox_network_open` and the
  instance does not open; an empty list refuses `sandbox_fence_unconfigured` and a probe
  that could not answer `sandbox_fence_unmeasured`. `deploy/fence.sh` installs the packet
  filter that makes it hold -- honestly "nothing of this house" rather than a provider
  allowlist, and the runbook says so. `deploy/README.md` is the whole order: build and
  pin both images, configure, make the network and the filter, seed the binaries and the
  logins, first start, upgrade, backup and restore, and where a resident's folders live
  on a host whose filesystem is volumes. ADR 0016 is accepted, with a Measured section
  naming every assumption the epic contradicted.

- A resident may run on a provider login of its own. `<data>/credentials/<resident
  id>/<kind>/` is a directory an operator seeds with that CLI's own login flow -- Hearth
  makes the shelf `0700` and never creates or copies a login. Admission resolves which
  one a run spends and writes `runs.login_scope` (schema 13, forward-filled to
  `household`: every run that predates the column spent the only login there was), the
  worker mounts that directory rather than the household's, and the receipt says which
  it was. The directory decides the scope and validity decides whether the run happens:
  a resident whose own login is empty, lapsed or taken away **waits** with
  `login_required` and never falls back to the household's, because falling back would
  spend a subscription the operator did not choose. That resident is held and nobody
  else, and it says so: a held run is audited once as `run.waiting`. Each login is probed
  the way the household's is -- `loggedIn` and nothing more, never a credential -- at
  start (a lapse audited once as `login.resident_lapsed`), on `GET /api/health` under
  `login.resident_lapsed`, and before that resident's run launches, with the answer
  remembered for a minute and shared between those paths so a held run does not start a
  CLI twice a second. A provider that could not be asked at all is
  `login.resident_unknown` rather than a lapse: its runs wait all the same, but nobody
  is sent to run a login flow they do not need. `python -m hearth credentials --data <dir>` lists
  every seeded login with its probe result and nothing else of the provider's answer,
  asking the stricter question a sandboxed burrow will ask. Townhall's resident view
  says whose login it is on per provider, and a finished run says which it spent.
  Bundles carry no login at all -- not the credential, not the directory, not the fact
  that there was one.

- What a resident may reach on disk is part of its management grant. `mounts` names at
  most sixteen folders with a mode, read-only unless the grant says `rw`, refused at
  write time (`grant_mount_forbidden`) for a relative path, for `/`, `/etc`, `/proc`,
  `/sys`, for anything containing or contained by Hearth's data directory, a runtime
  login or the container runtime's socket, and for a name or path two mounts share.
  Admission resolves the list into the run's own `run_mounts` at the grant's revision
  (schema 12; a folder the host lacks makes the run wait, `mount_unavailable`), the run's
  context lists what it reaches and where, and the launcher turns each into a bind mount
  at `/mounts/<name>` -- read-only unless writable, and nothing else of the host in the
  container. A run on the process launcher records the same list and reaches the host
  paths, so a laptop run says honestly what it would have had. A writable folder is
  surveyed before and after the session (names, sizes, times -- never content), the
  receipt says written, untouched or not known, and settlement audits
  `run.mount_rw_used` from what admission pinned; granting one audits
  `grant.mount_rw_granted`. Bundles carry a folder as a name and a mode with no path,
  and an import grants only what the operator's own map resolves. Townhall edits the
  folders in the grant and lists them on a finished run. Measured against Docker
  Desktop's Linux VM (`docs/evidence/sandbox-mounts-2026-09-09.json`): a read-only mount
  refuses a write with `Read-only file system`, a writable one takes it and the file is
  on the host owned by Hearth's uid, `/mounts` holds the grant and nothing else, and the
  folder they were carved out of does not exist inside the container.

- A Claude run executes inside the sandbox, and Hearth's own tools reach it there. The
  adapter names the CLI, the login and its `--mcp-config` by the paths the *session*
  sees: the image's own `claude`, a configuration directory of the run's own with the
  household's `.credentials.json` read-only inside it, and the bridge's two files
  mounted at the paths they already have -- so the shim, started by the image's own
  interpreter, connects to the same socket path Hearth wrote and the peer-credential
  check still holds across the boundary. The receipt says where the session ran, the pin
  a sandboxed run is held to is the image, what a worker started is written down before
  the runtime has named it and again after, and a container whose worker is gone is
  killed, removed and audited rather than left spending. A store on the container
  launcher whose login is not a *file* refuses at start: the macOS Keychain stays a
  convenience of the `process` launcher, as ADR 0016 said. Measured on a Linux Docker
  host against the Linux build of the pin (`docs/evidence/sandbox-claude-2026-09-09.json`,
  written up as spike 8 in `docs/claude-runtime.md`): a login on Linux is
  `.credentials.json` and nothing else is needed to read it; the CLI writes its own state
  into that directory, which is why it gets a tmpfs; the uid the sandbox runs as must
  exist in the image's own passwd file or the CLI dies at `uv_os_homedir` before its
  first byte; and the real shim, in a container, had `tools/list` answered over a mounted
  socket -- and was refused when it ran as another uid. The paid three-run journey on the
  sandbox waits on a Linux login only the account holder can make; the same three runs on
  the process launcher are `docs/evidence/claude-journey-process-2026-09-09.json`.

- A Codex run executes inside the sandbox, for real. The adapter names the CLI, the
  login and the file it writes its final message to by the paths the *session* sees:
  the image's own CLI, a login mounted at the path `CODEX_HOME` names, and one writable
  mount, so the receipt is still the CLI's own stream and the final message still
  reaches the worker that reads it. On the process launcher the command is unchanged,
  byte for byte. The pin a sandboxed run is held to is the image digest -- read inside
  the dispatch guard, refused before the launch and never after it -- and a finished run
  says where it happened: `sandbox: {launcher, container_id, image}` on the receipt. A
  container whose worker is gone is now stopped, removed and audited
  `sandbox.stray_removed` rather than left spending, and it is never adopted: the stream
  that was being priced died with the worker, so the run is unknown, never zero. What a
  run started is written down before the runtime has named it and again after, and once
  for each session a management run starts, because that file is the only thing that can
  find a container whose worker is gone. One
  real run on a Linux Docker host, `docs/evidence/sandbox-codex-journey-2026-09-09.json`.
  Three things it measured are in `docs/sandbox.md` and each of them failed every run it
  touched: the CLI cannot run with a read-only `CODEX_HOME` (it gets a tmpfs of its own
  now, with the household's credential read-only inside it), Codex on Linux needs the
  `bwrap` and code-mode-host executables its own package ships beside it, and `flock`
  does not exclude on a Docker Desktop bind mount from macOS -- so a lock alone no
  longer authorises killing a container, and the worker records its own pid beside it.
- There is one seam under the worker for starting a session, and a sandbox it can be
  pointed at. Neither live adapter calls `subprocess.Popen` on a provider CLI any more:
  `process` is byte for byte what Hearth has always done, and `container` starts the
  same command inside a container created for that run, from an image pinned by digest,
  on the operator's own network, read-only, on a tmpfs workspace, as Hearth's own uid.
  Which one is `HEARTH_SANDBOX`, and it travels in the run's request because the
  detached worker has a search path and nothing else. The image digest is pinned in
  `system_meta` with an audit fact, and the CLIs inside the image are hashed by the
  image's own `sha256sum` against the binary pins the store already holds, so
  `sandbox_image_changed`, `sandbox_image_unavailable`, `sandbox_network_missing`,
  `sandbox_runtime_unavailable` and `sandbox_binary_mismatch` are all refusals at start
  rather than one failed run at a time. `/health` names the launcher and the digest;
  the network and the reasons stay behind the operator's token. Measured against a real
  daemon (`docs/sandbox.md`, `docs/evidence/sandbox-2026-09-09.json`), and two of the
  measurements contradict what the decision assumed: killing the attached worker does
  **not** stop its container, and a unix socket cannot be bind-mounted from a Mac's
  filesystem into a container at all, though it works between containers over a volume,
  which is the shape the server runs in. No run executes in a container yet; this is the
  seam, the pins and the measurements the rest of the epic stands on.
- The Claude journey ran for real, and two things it found are fixed. The evidence
  file `docs/evidence/claude-journey-2026-09-09.json` records three runs on
  `claude_subscription` beside a Codex default, each settled to exactly the CLI's own
  `total_cost_usd`, with the journal written over the bridge and read back. On the way:
  the detached worker handed the CLI no `USER`, and on macOS the Keychain files the
  login under it, so every session answered `Not logged in` -- the session environment
  now carries the account name, read from the uid rather than inherited; and one API
  response streams as one assistant event per content block with the same id and usage,
  so the parser counts a response's cache-write split once instead of refusing to price
  a session that thought before it called a tool. The journey script no longer writes
  an evidence file for a journey that never reached a run.
- The operator is told which brain worked each run. Townhall stops naming a provider
  in its own markup: the snapshot carries the household's runtime table -- its default,
  what it is configured for, and how Hearth's registry names every kind a finished run
  may still carry -- plus each run's model and price schedule from its own pin, and
  every label is read from there. So the result panel says `CLAUDE RESULT` over a
  Claude run and `CODEX RESULT` over a Codex one (`SIMULATED ARTIFACT` over a kind
  that never was a provider), the rail names both brains, a resident's view says which
  one its work is admitted to, and the grant catalog names its execution profiles.
  A state that cannot say which runtimes a household has is refused rather than
  displayed. Beside that: `GET /health` names the runtimes this instance opened and
  `GET /api/health` adds, behind the operator's own token, every one that refused with
  the provider's reason -- because a run pinned to a runtime that is not configured
  here waits and nothing else surfaces it, while why a provider is missing is a fact
  about the operator's own machine; and a
  granted run's management protocol is its own runtime's transport
  (`codex_app_server`, `claude_mcp_bridge`) rather than one word for both. The Claude
  runtime's own document is finished with the operator's setup and login procedure,
  the refusal matrix and what the operator sees. And the Claude session's
  `--max-budget-usd` fence is the resident's remaining day rather than the run's
  reservation: every path in Hearth reserves a cent, a reservation is a hold and not a
  cap, and a one-cent provider stop would have ended every real session after its
  first billed request and settled it as failed.
- Which runtime a resident runs on is a declaration fact. `declarations.runtime`
  (schema 11, null = the store's default) is pinned onto every run at admission, and
  `system_meta.runtime_kind` becomes that default rather than the household's only
  answer: Karen can stay on `codex_subscription` while another resident on the same
  store runs on `claude_subscription`. One instance builds every live runtime it is
  configured for -- each through the registry's own module and class, so the two
  adapters are named in one place -- and the executor works each run with the runtime
  its own pin names. A second runtime that will not open — a lapsed login, a CLI past its
  pin — leaves the household standing and is recorded once at start as
  `runtime.unavailable`; a run pinned to a runtime this instance is not configured for
  waits rather than being handed to another provider or thrown away, and is worked as
  soon as that runtime is configured again. A declaration may only *move* to a runtime
  this store has really been configured for (`runtime_not_configured`) while keeping the
  one that already stands is always allowed, the execution profile an operator, Karen or
  a bundle names is that resident's runtime, and a bundle whose runtime the importing
  instance lacks keeps the resident on the default with the reason recorded. Recorded in
  `docs/adr/0015-runtime-per-resident.md`.
- Hearth's own tools reach a Claude run. `--mcp-config` names a Hearth-owned stdio shim
  (`python -I -m hearth.integrations.claude.mcp_bridge`) that carries no credential, no
  owner token and no database — it forwards `tools/list` and `tools/call` over a unix
  socket in the run's own folder, one connection per call, and hands back the reply. The
  trusted worker answers on the other end through `management.bridge`, authenticating
  with `BoundRun` and mutating and auditing in one transaction, exactly as the Codex
  adapter does over the app server's dynamic tools. The socket is 0600 inside the run's
  0700 folder and every connection's peer uid is checked from the kernel. `--tools` and
  `--allowedTools` name exactly the `mcp__hearth__*` tools the run's grant allows — both
  flags, because one grants existence and the other permission — and the session's own
  `init` event has to report Hearth's server as connected with exactly those tools, the
  pinned model and the pinned build, or the session is stopped before its first turn
  (`claude_tools_changed`, `claude_session_unpinned`). The launch pins travel into
  `run_management` and into the receipt, and settlement refuses
  `management_configuration_changed` when they disagree; a failed bridge is recorded as
  `mcp_bridge_failed` and settles as failed, never as unknown with a relaunch. So a
  resident that writes its own memory and journal, holds a grant, works a letter or holds
  post is now admitted on a Claude store — Karen included — and a memory-writable
  resident's journal entry travels through `hearth_journal_write` over the bridge under
  the grantless-pin rules of ADR 0012. One real session was recorded against the pinned
  CLI to check the design rather than assume it, and it corrected a #146 reading:
  `usage.iterations` is a partial view of a session's requests, not the list of them, so
  rows that do not add up to the model's total are priced from that total instead of
  leaving the run unpriced — without which every management run on Claude would have
  settled with unknown usage. `docs/claude-runtime.md` carries the measurements.
- A resident's work runs on Claude. A run pinned to `claude_subscription` launches the
  pinned CLI exactly once, from a detached worker that holds its own folder's lock, with
  the prompt on stdin and the bounded flag set; the CLI's original `stream-json` output
  is the receipt, kept whole. Cost is Hearth's own arithmetic over the session's token
  counts under the pinned schedule `claude-opus-5-api-equivalent-2026-09-07`, and it
  settles only when it matches both numbers the CLI reports — each model's `costUSD` and
  the session's `total_cost_usd` — per model and in total. A wider disagreement, a model
  the schedule does not price, or a cache write whose tier the stream never named leaves
  usage unknown and the resident's hold in place, while the answer is still kept. The
  run's reserved budget is also passed to the CLI's own `--max-budget-usd` fence: it
  bills the request that crosses it before stopping, so a budget stop settles as failed
  with the cost known, and Hearth's admission hold stays the authority. Cancellation
  signals the worker's own process group, and the only zero-cost ending is a launch that
  provably never happened. `docs/claude-runtime.md` gains what #146 measured, including
  three findings against the plan: the result event's `usage` block is truthful on a
  success and zeroed on a budget stop, an assistant message's `output_tokens` is a
  mid-stream snapshot, and there is no long-context tier on this schedule — confirmed
  from the Anthropic pricing page, which is also why the second model a session really
  spends, `claude-haiku-4-5`, is priced at its own published rates rather than ignored.
  One thing a Claude store cannot do yet: a run pinned to reach Hearth's own management
  tools — its resident holds a grant, declares `memory_writable`, works a letter or holds
  post — is refused `run_management_unsupported` at admission, because the bridge that
  carries those tools into a Claude session has not landed. It is refused where nothing
  has been spent rather than launched into a session holding none of the authority its
  declaration promised. A stored price pin is also now read by the runtime the run was
  pinned to, instead of by whichever schedule happens to recognise it. A session the
  stream proves billed nothing — no model usage, a zero total, no requests and no API
  response — settles at zero rather than at unknown, so a subscription login that lapses
  fails its runs without holding every resident's allowance behind a manual
  reconciliation; and a lapsed login is now reported as `claude_subscription_login_required`,
  because `claude auth status --json` prints its answer and exits 1 when there is none.

- Hearth knows a second live runtime kind. One registry in
  `integrations/interface.py` now answers every question about a runtime kind — what is
  live, what settles from receipts, whose start is replayable, which adapter and which
  price schedule — replacing the kind checks each call site carried, and the four kinds
  Hearth already knew answer exactly as they did. `claude_subscription` joins it: a store
  can record it, a run can be pinned to it, and `HEARTH_CLAUDE_BINARY` /
  `HEARTH_CLAUDE_CONFIG_DIR` configure it the way the Codex pair does. Configuring it
  checks the pinned CLI version, checks that the private `CLAUDE_CONFIG_DIR` reports a
  login, and pins the binary's sha256 — refusing `claude_subscription_version_unsupported`,
  `claude_subscription_login_required` or `claude_subscription_binary_changed` and
  writing nothing when it does. No run executes on it yet, and a runtime that cannot
  price its work admits none: a store configured for Claude refuses `run_pricing_required`
  at admission until its price schedule lands, rather than creating runs that could only
  settle at a number nobody can check. `docs/claude-runtime.md`
  records the spike and every flag measurement behind it, including the two that came
  back against the plan: `--bare` cannot run under a subscription at all, and
  `--max-budget-usd` stops a session only after a request has already been billed past it.

- A new resident is proposed $1.00 a day: Townhall's New resident form opens at
  1,000,000 microdollars instead of 100,000, and Karen's seeded **Create residents**
  wording proposes the same unless the operator or the resident's purpose says otherwise
  and never less than one run costs. It is a proposal, not a floor — the operator still
  edits the number, `daily_limit` stays required on the API, and existing residents,
  `max_daily_limit` and the household allowance are untouched. The skill text is seeded
  once at explicit setup, so an already-bootstrapped household keeps its own revision.

- The letters door is turned from Townhall. The Letters section on a resident's page
  carries one control that opens or shuts the declared `letters.accept` door, names the
  declaration revision that now carries it, and shows a refusal where it was written
  rather than as a door that quietly did not move; a restored copy cannot turn one. The
  door travels alone: `PUT /api/residents/{id}` now takes a body carrying only
  `letters_accept` and `expected_revision`, so a control that never read a purpose or a
  skill text cannot overwrite one. What a resident *is* still changes whole or not at all
  — a body saying some of the five declaration fields and not the rest is refused as
  `declaration_fields_invalid` instead of merged into what stands, and so is a body that
  says nothing at all, because a revision nobody asked for spends the expected revision
  every other client is holding. A save carrying the whole declaration, and `python -m
  hearth save-resident`, behave exactly as they did.

- Letters are written down. `docs/letters.md` is the contract — the grant and the door
  that both have to be open, what Hearth arbitrates and the refusal each guard leaves,
  delivery by ordinary admission, the four states a letter ends in, cost by origin, the
  operator surfaces, and what is deliberately not built (no chat, no auto-wake, no
  broadcast, no delivery daemon). ADR 0011 records why a letter is a task rather than a
  message bus, why delivery is pull-based and asynchronous, why the depth cap defaults to
  two, why there is no free-form chat between residents, and the departure from the
  rebuild plan's "no delegation" line, which now points at the ADR and at the permission
  contract. The etiquette is library text rather than prompt prose: **Ask a colleague**
  and **Answer a letter** are seeded as ordinary editable skills that grant nothing,
  adopting an operator-written entry of the same name rather than seeding a second — Karen
  carries the asking one because her grant carries `send_letters`, and the answering one
  waits in the library for the operator that opens a door to assign it. Karen's setup
  seeds them, and because setup runs once, a household already set up before letters
  existed is seeded on start instead, so both wait in a library the operator can actually
  read rather than in one only a fresh data directory would ever get. The
  real journey now has a deterministic twin in CI walking the same three runs, and the doc
  carries #111's run ids and its 157,062 microdollars, the two prerequisites that journey
  found — a door closed by default, and a receiver's daily limit that has to cover a whole
  answering run — and the wart that the send receipt carries the task id, which is the
  letter id.

- People can see letters now. A resident's page carries a **Letters** section: everything
  that reached it and everything it wrote, each in one named state — answered, open,
  worked and never answered, failed, gone stale — on the Ledger's own colour-as-state,
  with the answer's text and a link to the run that wrote it. Beside the list stand the
  two things that quietly stop an answer, read from the resident rather than guessed: a
  shut door, which refuses every letter written here, and a daily limit, which is what an
  answering run has to fit inside before it is refused at allocation. The operator writes
  with its own hand from the same panel, under a command identity that survives an
  uncertain response, and a refusal is shown where it was written rather than as a
  disappearance. A task that is a letter shows the chain it belongs to, root first, with
  the name of whoever wrote each hop; a task that started its own chain shows none. A run
  that was refused a letter carries that refusal in its own evidence with the structured
  reason and the numbers it named, because a refusal writes nothing else down. The
  snapshot gained `letter_sent` and `letter_replied` events with both ends named, and
  Hamlet walks a villager from one door to the specific neighbour's from those events and
  from nothing else — once per event, from Townhall when the operator wrote it, and not
  at all for a resident that has left the village. An empty post is a still village.

- A letter now ends in one honest state and the answer reaches the resident that asked
  (schema 9, context 9). When the run working a letter settles, the letter settles with
  it, in the same transaction: `replied` when an answer was written, `unanswered` when
  the run succeeded and never called the reply tool, `failed` when the run did not
  finish, and `expired` when it went stale before anybody started it — each audited under
  its own name and linked to the letter, its root task and the run. A cancelled run is a
  run that did not finish, so its letter says `failed` too: cancelling is not going stale,
  and the one place a letter task ends outside its own run's settlement — a store adopting
  the one runtime over work no surviving runtime can observe — closes the letter in the
  same transaction, with the reason recorded on the letter's own fact. An answer written by
  a run that then fails still counts as an answer, because the sender has it; the run's
  own status is recorded beside the state rather than hidden by it. Nothing wakes the
  sender: its next run opens with a bounded, neutralized "replies since your last run"
  section built from the answers to its own letters, read at the same immutable edges as
  the rest of its pinned context — a skill example opens with none of it, being a
  rehearsal on exactly what its request named — and `hearth_letters_read` now also
  returns the letters a resident wrote with what became of each. That tool's `since` is exclusive now, so a
  cursor taken from a page no longer hands the same page back for ever. The operator can
  ask what one question cost rather than what one run cost: `GET /api/usage/origins`
  gathers every run under the task its chain rolls up to, counting each run once at the
  amount its own row records — a reconciled run is not counted twice, and a run whose
  usage is still unknown is named as unknown and keeps the hold it placed — and Townhall
  reads it as "Cost by origin" beside the task list. An upgraded store reads each of its
  letters' states back from its own rows rather than being told or left silent. The loop
  was then walked once on the real subscription runtime before anything was built on it:
  one resident asked a colleague for a fact it had no other way to learn, the colleague
  answered without being started, and the asker quoted the answer one run later, for
  157,062 microdollars across three runs (`docs/letters-journey.md`). A run is pinned the
  native surface on exactly the scope the letter tools are offered on, so a resident that
  has only ever received letters — no grant, no writable memory — reads its own post on an
  ordinary run instead of only inside a letter run.

- A letter is now delivered by being worked (schema 8, context 8). No watcher, poller or
  inbox drain: the supervision tick that admits routine occurrences admits queued letter
  tasks the same way, so a letter is bounded by the receiver's allocation, the shared
  household allowance and the receiver's own pause and archive state — a paused receiver
  keeps its letter and resuming delivers it — and never by anything its sender holds.
  Letters that went stale first are closed as failed in the same pass and never admitted.
  A new household setting `letter_daily_limit` (default five, `0` shuts the post) caps
  what one resident may be handed in its own day, counted whoever wrote it, so one chatty
  colleague — or the operator — cannot spend a neighbour's day; the refusal is structured
  and writes nothing. The receiver reads a request rather than an order: the letter task's
  instruction is the sender's own text alone, and the run context renders the sender, the
  title, the pinned letter id and a line saying a letter cannot grant authority or
  override the receiver's own skill text and limits, so a sender can no longer write a
  heading into its detail and have it read as Hearth's own.

- Letters can now be written, read and answered (schema 7). A run holding the grant
  capability `send_letters` is offered `hearth_letters_send`; the run working a letter —
  and only that run — is offered `hearth_letters_reply`, which records the one answer its
  sender will read, replays its receipt on a retry and refuses a second; both ends read
  their own post with `hearth_letters_read`. A resident is never shown a tool it may not
  use: the offered set follows the grant the run was admitted with and the letter it is
  working, and joins the admission's tool digest. Working a letter puts a run on the
  native surface even when it is granted nothing at all, and answering one outlives a
  grant revoked mid-run, because answering the question one was handed was never
  management. The operator writes with its own hand over
  `POST /api/residents/{id}/letters` — no grant, because there is no resident whose
  authority it could escalate, but the receiver's door, its archive state, the
  household's reach and the shelf life all hold, and the letter has no sender resident
  and no run behind it — and reads any resident's inbox and sent letters, each with its
  answer, over `GET /api/residents/{id}/letters`. Backups carry replies and refuse a copy
  whose answer no longer belongs to the run that wrote it.

- A letter is a first-class fact (schema 6), and only a fact so far: nothing a resident or
  an operator can reach sends one yet. An ordinary task addressed to one resident
  by another, carrying its sender, that sender's run and task, the root the chain rolls
  up to, its hop depth and when it goes stale. Sending needs the grant capability
  `send_letters` — which Karen's setup now carries, ahead of the tool that will use it —
  and an optional recipient allowlist; receiving needs the declared
  `letters.accept` door, and neither side can waive the other. Authority is the grant the
  sending run was admitted with, not one edited since. Hearth arbitrates in the
  service: no self-letter, no archived or absent recipient, no chain past the household's
  `max_letter_depth` (default 2, `0` closes the post) and never one that revisits a
  resident — depth and lineage read from the sender's own admitted run, so a forged
  parent buys nothing. Every letter expires (household `letter_ttl_seconds`, one day by
  default; a sender may shorten it, never lengthen it), is never admitted after that and
  is closed as a failed task. Refusals are structured and write nothing, backups carry
  the lineage, and upgrading writes the new grant scope into every stored grant and the
  admissions that pinned one without opening a single door.

- Docs describe one runtime and no mocks. The last seven mock documents and ADR 0004 are
  deleted, `implementation.md`'s mock and container-rehearsal checkpoints collapse into
  one record of what the epic shipped, and the remaining incidental mentions of mock
  runtimes, `simulated`, approvals, the noticeboard and the Skill evaluator are corrected
  across `docs/`, `README.md`, `AGENTS.md` and `CONTEXT.md`. ADR 0014 records the
  decision.

- Skill examples run as the resident that asked for them (schema 5): the Skill evaluator
  service resident, its empty-memory rule and its provisioning escape hatch are gone. A
  validation pins the requesting resident, its declaration and memory revisions and the
  context version, and its two cases are admitted on that resident with no management
  tools. They need the run slot the requesting turn is holding, so a resident asks, ends
  its turn and reads the evidence in a later one; an operator names whose examples these
  are. Upgrading renames the stored runner, archives any evaluator with an audit fact and
  fails the validations that were still waiting on it.

- Approvals and publication are gone (schema 4): no grants, requests, decisions, broker
  or noticeboard until a real approval-gated effect needs them. The inbox becomes the
  feature they were attached to: every notification is written with the work it reports,
  kept, marked read or unread, and shown on its own Townhall page. Upgrading keeps every
  run notification and removes the ones announcing a review that no longer exists.

- A backup is the household and nothing else: `hearth.db`, artifacts, resident memory
  and archived journal entries. The local inbox and noticeboard folders are no longer
  copied or verified, so a restored copy no longer carries their files.
- The `simulated` flag is gone from artifacts (schema 3), run context, snapshot, API
  responses, the backup manifest and the UI; the reconciliation source is now
  `operator_reported`, and an upgrade may drop a column only if the release lists it.
  Upgrading rewrites the run context, so a run admitted but never launched is asked to
  cancel and settles at zero. Backups pin the schema version: re-capture after upgrading,
  because a backup taken before this release can no longer be restored.
- The Codex subscription is the only runtime: mock runtimes, their stores and the
  runtime/process-boundary selectors are gone, tests drive a fake under `tests/`,
  and a store recorded against a removed runtime adopts the one runtime on start
  while its finished runs keep their own pin.
- Older Hearth stores upgrade forward on start with the original kept beside them;
  a quiet store may change runtime kind; docs rule trimmed to ADR + changelog (ADR 0013).
