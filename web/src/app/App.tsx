import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Client,
  RequestError,
  StateFormatError,
  type PendingTask,
  type Snapshot,
} from "../shared/client";
import "./style.css";
import { RoutinePanel } from "../features/routines/Routines";
import { UsageByOrigin, UsageReport } from "../features/tasks/UsageReport";
import { ResidentMaintenance } from "../features/residents/Maintenance";
import { MemoryHistory } from "../features/residents/MemoryHistory";
import { Journal } from "../features/residents/Journal";
import {
  NewResident,
  ProfileProvenance,
} from "../features/residents/NewResident";
import { ManagementPanel } from "../features/management/Management";
import { InputLibrary } from "../features/inputs/Inputs";
import { RunInputs } from "../features/inputs/Selection";
import { SkillCatalog } from "../features/skills/SkillCatalog";
import { HouseholdPanel } from "../features/household/Household";
import { ImportResident } from "../features/residents/ImportResident";
import { Letters } from "../features/letters/Letters";
import { Lineage } from "../features/letters/Lineage";
import { Hamlet } from "../features/hamlet/Hamlet";
import {
  configuredKinds,
  resultEyebrow,
  runtimeLabel,
  runtimeNames,
} from "../shared/runtimes";

const SESSION_KEY = "hearth.operator-token";
function savedToken(): string | null {
  try {
    return sessionStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}
function saveToken(value: string | null) {
  try {
    if (value === null) sessionStorage.removeItem(SESSION_KEY);
    else sessionStorage.setItem(SESSION_KEY, value);
  } catch {
    /* Storage may be disabled; the current login still works. */
  }
}

const statusLabel = (s: string) =>
  ({
    ready: "Ready for work",
    starting: "Starting",
    running: "Working",
    stopping: "Stopping",
    interrupted: "Outcome unknown",
    succeeded: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
    queued: "Queued",
    paused: "Paused",
  })[s] ?? s;
const clock = (s: number) =>
  new Date(s * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

function Emblem() {
  return (
    <svg viewBox="0 0 40 40" aria-hidden="true">
      <path d="M20 3 4 16v20h32V16Z" fill="currentColor" />
      <path
        d="M20 14c2 5 7 7 7 12a7 7 0 0 1-14 0c0-4 4-7 7-12Z"
        fill="#f4f0e7"
      />
      <path d="M20 23c1 3 3 4 3 6a3 3 0 0 1-6 0c0-2 2-3 3-6Z" fill="#b44f32" />
    </svg>
  );
}

function SummaryOutput({
  content,
  eyebrow,
  onClose,
}: {
  content: string;
  eyebrow: string;
  onClose: () => void;
}) {
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    panel.current?.focus({ preventScroll: true });
    panel.current?.scrollIntoView?.({ block: "start", behavior: "instant" });
  }, [content]);
  return (
    <section
      ref={panel}
      tabIndex={-1}
      className="output"
      aria-label="Summary output"
    >
      <div className="section-title">
        <span className="eyebrow">{eyebrow}</span>
        <button className="quiet" onClick={onClose}>
          Close ×
        </button>
      </div>
      <pre>{content}</pre>
    </section>
  );
}

type Page =
  | "townhall"
  | "residents"
  | "resident"
  | "new-resident"
  | "import-resident"
  | "skills"
  | "inputs"
  | "management"
  | "tasks"
  | "routines"
  | "inbox"
  | "activity"
  | "hamlet";

export function App() {
  const [client, setClient] = useState<Client | null>(null);
  const [token, setToken] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [view, setView] = useState<Page>("townhall");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [residentId, setResidentId] = useState("");
  const [provisionId, setProvisionId] = useState("");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [instruction, setInstruction] = useState("Summarize today’s notes.");
  const [output, setOutput] = useState<{
    content: string;
    residentId?: string;
    // The runtime the run was pinned to, so the result is credited to the provider
    // that actually produced it rather than to whatever the household runs on today.
    runtimeKind?: string;
  } | null>(null);
  const pending = useRef<PendingTask | null>(null);
  const currentSession = useRef<Client | null>(null);

  function publish(next: Snapshot) {
    setSnapshot((previous) =>
      previous?.epoch === next.epoch && previous.cursor > next.cursor
        ? previous
        : next,
    );
  }
  function lock() {
    saveToken(null);
    currentSession.current?.clear();
    currentSession.current = null;
    setClient(null);
    setSnapshot(null);
    setOutput(null);
    setConnected(false);
    setBusy(false);
  }
  function fail(e: unknown, source: Client | null = client) {
    if (source !== currentSession.current) return;
    setError(e instanceof Error ? e.message : "Something went wrong.");
    if (
      (e instanceof RequestError && e.status === 401) ||
      e instanceof StateFormatError
    ) {
      lock();
    }
  }
  useEffect(() => {
    if (!client) return;
    setConnected(false);
    const controller = new AbortController();
    void client
      .watch(
        controller.signal,
        (next) => {
          if (currentSession.current === client) publish(next);
        },
        (connected) => {
          if (currentSession.current === client) setConnected(connected);
        },
      )
      .catch((e) => fail(e, client));
    return () => controller.abort();
  }, [client]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      if (client && currentSession.current === client) {
        const next = await client.state();
        if (currentSession.current === client) publish(next);
      }
    } catch (e) {
      fail(e);
    } finally {
      if (currentSession.current === client || currentSession.current === null)
        setBusy(false);
    }
  }
  async function connect(credential: string) {
    setBusy(true);
    setError("");
    const candidate = new Client(credential);
    currentSession.current = candidate;
    try {
      const next = await candidate.state();
      if (currentSession.current !== candidate) return;
      saveToken(credential);
      setSnapshot(next);
      setClient(candidate);
      setToken("");
    } catch (e) {
      candidate.clear();
      fail(e, candidate);
    } finally {
      if (
        currentSession.current === candidate ||
        currentSession.current === null
      )
        setBusy(false);
    }
  }
  async function login(event: FormEvent) {
    event.preventDefault();
    await connect(token);
  }
  useEffect(() => {
    const credential = savedToken();
    if (credential) void connect(credential);
    return () => {
      currentSession.current?.clear();
      currentSession.current = null;
    };
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!client) return;
    if (!pending.current)
      pending.current = {
        id: crypto.randomUUID(),
        body: {
          resident_id: residentId,
          instruction,
          expires_at: Math.floor(Date.now() / 1000) + 3600,
        },
      };
    await act(async () => {
      let receipt;
      try {
        receipt = await client.submit(pending.current!);
      } catch (error) {
        if (error instanceof RequestError && error.status === 410)
          pending.current = null;
        throw error;
      }
      if (currentSession.current === client) {
        pending.current = null;
        await client.start(receipt.task_id);
      }
    });
  }
  useEffect(() => {
    const openLinkedView = () => {
      let hash: string;
      try {
        hash = decodeURIComponent(window.location.hash).replace(
          /^#runs\//,
          "#run-",
        );
      } catch {
        setError("Invalid notification link");
        return;
      }
      if (hash === "#new-resident" || hash.startsWith("#new-resident/")) {
        setProvisionId(hash === "#new-resident" ? "" : hash.slice(14));
        setView("new-resident");
      } else if (hash === "#import-resident") {
        setView("import-resident");
      } else if (hash === "#residents-archived") {
        setIncludeArchived(true);
        setView("residents");
      } else if (hash.startsWith("#management/")) {
        setView("management");
      } else if (hash.startsWith("#inputs/")) {
        setView("inputs");
      } else if (hash.startsWith("#skills/")) {
        setView("skills");
      } else if (hash.startsWith("#residents/")) {
        setResidentId(hash.slice(11));
        setView("resident");
      } else if (
        [
          "#townhall",
          "#hamlet",
          "#residents",
          "#skills",
          "#inputs",
          "#management",
          "#tasks",
          "#routines",
          "#inbox",
          "#activity",
        ].includes(hash)
      ) {
        setView(hash.slice(1) as Page);
      }
      if (hash.startsWith("#run-") && client) {
        setView("tasks");
        const id = hash.slice(5);
        void act(async () => {
          const run = await client.run(id);
          const content = run.artifact_id
            ? (await client.artifact(run.artifact_id)).content
            : `Run ${run.status}`;
          if (currentSession.current === client)
            setOutput({ content, runtimeKind: run.runtime_kind });
        });
      }
    };
    openLinkedView();
    window.addEventListener("hashchange", openLinkedView);
    return () => window.removeEventListener("hashchange", openLinkedView);
  }, [client]);
  useEffect(() => {
    const hash = window.location.hash.replace(/^#runs\//, "#run-");
    if (hash.startsWith("#run-"))
      document
        .getElementById(hash.slice(1))
        ?.scrollIntoView?.({ block: "center" });
  }, [view, snapshot]);
  const residents = snapshot?.residents ?? [];
  const current = residents.find((r) => r.id === residentId);
  const visibleTasks = (snapshot?.tasks ?? []).filter(
    (task) => view !== "resident" || task.resident_id === residentId,
  );
  // A `#run-<id>` anchor lands on a row in Tasks & results, so it can only open a run
  // whose task is still listed there. Older work is named rather than linked.
  const openableRun = (runId: string) =>
    (snapshot?.runs ?? []).some(
      (run) =>
        run.id === runId &&
        visibleTasks.some((task) => task.id === run.task_id),
    );
  const completed =
    snapshot?.runs.filter((r) => r.status === "succeeded").length ?? 0;
  const active =
    snapshot?.runs.filter((r) =>
      ["starting", "running", "stopping", "interrupted"].includes(r.status),
    ).length ?? 0;
  const notifications = snapshot?.notifications ?? [];
  const unread = notifications.filter((n) => n.read_at === null).length;
  const troubledRuns =
    snapshot?.runs.filter((r) =>
      ["failed", "interrupted"].includes(r.status),
    ) ?? [];
  const failedSetups =
    snapshot?.provisioning?.filter((item) => item.status !== "ready") ?? [];
  const pausedResidents = residents.filter(
    (r) => r.presence === "paused" || r.pause_reason,
  );
  const attentionCount =
    troubledRuns.length + failedSetups.length + pausedResidents.length;

  const pageTitle =
    view === "resident"
      ? (current?.name ?? "Resident not found")
      : view === "new-resident"
        ? "New resident"
        : view === "townhall"
          ? "Townhall"
          : view[0].toUpperCase() + view.slice(1);
  const pageNote = {
    townhall: "Residents, their work, and what needs your attention.",
    residents: "Everyone who lives here.",
    resident: "",
    "new-resident": "Purpose, memory and skills become one complete resident.",
    "import-resident":
      "Bring a resident definition from another Hearth. Runs, history and authority never travel with it.",
    skills: "Reusable instructions, revision history and shared know-how.",
    inputs: "Notes each resident is allowed to read.",
    management: "Which residents may create and assign work, within limits.",
    tasks: "Every assignment and its result.",
    routines: "Scheduled work.",
    inbox: "Everything Hearth has told you, unread first.",
    activity: "Everything Hearth recorded, newest first.",
    hamlet: "Your residents at home.",
  }[view];

  const navGroups: { label?: string; pages: Page[] }[] = [
    { pages: ["townhall", "residents", "tasks", "inbox", "activity"] },
    { label: "Library", pages: ["skills", "inputs", "routines", "management"] },
    { label: "Village", pages: ["hamlet"] },
  ];
  const navLabel = (page: Page) =>
    page === "townhall" ? "Townhall" : page[0].toUpperCase() + page.slice(1);
  const navBadge = (page: Page) =>
    page === "tasks" ? active : page === "inbox" ? unread : 0;
  const isSelected = (page: Page) =>
    view === page ||
    (page === "residents" && (view === "resident" || view === "new-resident"));

  const residentTable = snapshot && client && (
    <>
      {residents.length > 0 && (
        <div className="table-wrap">
          <table className="ledger">
            <thead>
              <tr>
                <th>Resident</th>
                <th>Status</th>
                <th>Daily limit</th>
                <th>Declaration</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {residents
                .filter(
                  (r) => includeArchived || r.lifecycle?.state !== "archived",
                )
                .map((r) => (
                  <tr key={r.id}>
                    <td>
                      <strong>{r.name}</strong>
                      <span className="id">{r.id}</span>
                      <p className="purpose">{r.purpose}</p>
                    </td>
                    <td>
                      <span className={`state state-${r.presence}`}>
                        {connected
                          ? statusLabel(r.presence)
                          : "Last known: " + statusLabel(r.presence)}
                      </span>
                    </td>
                    <td className="num">
                      ${(r.daily_limit / 1e6).toFixed(2)}
                      <small>{r.budget_timezone ?? "UTC"}</small>
                    </td>
                    <td className="num">rev {r.revision}</td>
                    <td>
                      <a href={`#residents/${encodeURIComponent(r.id)}`}>
                        <span className="sr-only">{r.name} · </span>View
                        resident →
                      </a>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
      {!residents.length && (
        <div className="empty">
          <h3>No residents yet</h3>
          <p>Define a purpose and create your first resident.</p>
          {!snapshot.restore_hold && (
            <p>
              <a href="#new-resident">Create a new resident →</a>
            </p>
          )}
        </div>
      )}
    </>
  );

  const setupFailures = failedSetups.map((item) => (
    <section
      key={item.command_id}
      className="provision-failure"
      aria-label={`Setup for ${item.name}`}
    >
      <strong>
        {item.name} · {item.status === "failed" ? "Setup failed" : "Setting up"}
      </strong>
      <p>{item.reason?.replaceAll("_", " ")}</p>
      <a href={`#new-resident/${encodeURIComponent(item.command_id)}`}>
        Inspect setup and retry →
      </a>
    </section>
  ));

  return (
    <>
      <header className="rail">
        <a className="brand" href="#townhall" aria-label="Hearth home">
          <Emblem />
          <span>
            Hearth
            <span className="brand-note">Townhall</span>
          </span>
        </a>
        <nav aria-label="Main views">
          {navGroups.map((group, index) => (
            <div key={index} style={{ display: "contents" }}>
              {group.label && <span className="nav-group">{group.label}</span>}
              {group.pages.map((page) => (
                <a
                  key={page}
                  href={`#${page}`}
                  className={isSelected(page) ? "selected" : ""}
                  aria-current={isSelected(page) ? "page" : undefined}
                >
                  {navLabel(page)}
                  {navBadge(page) > 0 && (
                    <span className="nav-badge">{navBadge(page)}</span>
                  )}
                </a>
              ))}
            </div>
          ))}
        </nav>
        <div className="rail-foot">
          {snapshot && (
            <span className={`chip ${connected ? "live" : "off"}`}>
              {connected
                ? `Connected to ${runtimeNames(snapshot.runtimes).join(" and ")}`
                : "Reconnecting · state may be stale"}
            </span>
          )}
          <span>
            {snapshot
              ? configuredKinds(snapshot.runtimes)
                  .map((kind) => runtimeLabel(snapshot.runtimes, kind))
                  .join(" · ")
              : "Local operator console"}
          </span>
          {client && (
            <button className="quiet" onClick={lock}>
              Lock
            </button>
          )}
        </div>
      </header>
      <main>
        <div className="page-head">
          <div>
            {view === "resident" && (
              <div className="crumb">
                <a href="#residents">← All residents</a>
              </div>
            )}
            <h1>{pageTitle}</h1>
            {pageNote && <p>{pageNote}</p>}
          </div>
          {snapshot && client && (
            <div className="page-actions">
              {(view === "townhall" || view === "residents") &&
                !snapshot.restore_hold && (
                  <>
                    <a className="btn" href="#import-resident">
                      Import resident
                    </a>
                    <a className="btn primary" href="#new-resident">
                      New resident ＋
                    </a>
                  </>
                )}
            </div>
          )}
        </div>
        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
        {!client || !snapshot ? (
          <section className="entry">
            <span className="eyebrow">Welcome home</span>
            <h2>Open the gate</h2>
            <p>
              Enter your local operator token to open Hearth. This tab stays
              signed in across refreshes until you select Lock or close it.
            </p>
            <form onSubmit={login}>
              <label htmlFor="token">Operator token</label>
              <input
                id="token"
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                required
                autoComplete="off"
              />
              <button className="primary" disabled={busy}>
                Enter Hearth <span>↗</span>
              </button>
            </form>
          </section>
        ) : (
          <>
            {snapshot.restore_hold && (
              <div className="notice" role="status">
                Restored copy · read-only. Execution, scheduling and effects are
                disabled.
              </div>
            )}
            {view === "townhall" && attentionCount > 0 && (
              <section className="attention" aria-label="Needs your attention">
                <ul>
                  {troubledRuns.map((run) => (
                    <li key={run.id}>
                      <span>
                        <strong>
                          Run {statusLabel(run.status).toLowerCase()}
                        </strong>
                        {run.resident_id}
                      </span>
                      <a href={`#run-${encodeURIComponent(run.id)}`}>
                        Open run →
                      </a>
                    </li>
                  ))}
                  {failedSetups.map((item) => (
                    <li key={item.command_id}>
                      <span>
                        <strong>{item.name}</strong>
                        {item.status === "failed"
                          ? "setup failed"
                          : "still setting up"}
                        {item.reason
                          ? ` · ${item.reason.replaceAll("_", " ")}`
                          : ""}
                      </span>
                      <a
                        href={`#new-resident/${encodeURIComponent(item.command_id)}`}
                      >
                        Inspect →
                      </a>
                    </li>
                  ))}
                  {pausedResidents.map((r) => (
                    <li key={r.id}>
                      <span>
                        <strong>{r.name}</strong>
                        paused
                        {r.pause_reason
                          ? ` · ${r.pause_reason.replaceAll("_", " ")}`
                          : ""}
                      </span>
                      <a href={`#residents/${encodeURIComponent(r.id)}`}>
                        Open →
                      </a>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {view === "townhall" && (
              <div className="stats">
                <div className="stat">
                  <span>Residents</span>
                  <strong>{residents.length}</strong>
                </div>
                <div className="stat">
                  <span>Active runs</span>
                  <strong>{active}</strong>
                </div>
                <div className="stat">
                  <span>Completed runs</span>
                  <strong>{completed}</strong>
                  <em>recent history</em>
                </div>
                {snapshot.household && (
                  <div className="stat">
                    <span>Allowance left today</span>
                    <strong>
                      ${(snapshot.household.remaining / 1e6).toFixed(2)}
                    </strong>
                    <em>
                      of ${(snapshot.household.daily_limit / 1e6).toFixed(2)}
                    </em>
                  </div>
                )}
              </div>
            )}
            {view === "new-resident" && (
              <NewResident
                key={`${snapshot.epoch}:${provisionId}`}
                client={client}
                readOnly={snapshot.restore_hold === true}
                commandId={provisionId}
                onCreated={async (receipt) => {
                  const route = window.location.hash;
                  const next = await client.state();
                  if (currentSession.current !== client) return;
                  publish(next);
                  if (
                    next.epoch === snapshot.epoch &&
                    window.location.hash === route
                  )
                    window.location.hash = `#residents/${receipt.resident_id}`;
                }}
              />
            )}
            {view === "import-resident" && (
              <ImportResident
                key={snapshot.epoch}
                client={client}
                readOnly={snapshot.restore_hold === true}
              />
            )}
            {(view === "townhall" || view === "residents") && setupFailures}
            {view === "management" && (
              <ManagementPanel
                key={snapshot.epoch}
                client={client}
                readOnly={snapshot.restore_hold === true}
                onChanged={() => void act(async () => {})}
              />
            )}
            {view === "inputs" && (
              <InputLibrary
                key={snapshot.epoch}
                client={client}
                readOnly={snapshot.restore_hold === true}
              />
            )}
            {view === "skills" && (
              <SkillCatalog
                key={snapshot.epoch}
                client={client}
                residents={snapshot.residents}
                readOnly={snapshot.restore_hold === true}
              />
            )}
            {view === "hamlet" && (
              <Hamlet snapshot={snapshot} connected={connected} />
            )}
            {(view === "townhall" ||
              view === "residents" ||
              view === "hamlet") && (
              <section
                className="resident-directory"
                aria-label="Resident directory"
              >
                <div className="section-label">
                  <span>Residents</span>
                  {view === "residents" && (
                    <label className="archive-filter">
                      <input
                        type="checkbox"
                        checked={includeArchived}
                        onChange={(e) => setIncludeArchived(e.target.checked)}
                      />
                      Include archived residents and history
                    </label>
                  )}
                </div>
                {residentTable}
              </section>
            )}
            {view === "townhall" && snapshot.household && (
              <HouseholdPanel
                client={client}
                policy={snapshot.household}
                readOnly={snapshot.restore_hold === true}
                onSaved={() => {
                  void client.state().then(publish).catch(fail);
                }}
              />
            )}
            {view === "resident" && current && (
              <>
                <section
                  className="profile-facts"
                  aria-label="Resident information"
                >
                  <div>
                    <span className="eyebrow">Purpose</span>
                    <h2>{current.purpose}</h2>
                    <span className={`state state-${current.presence}`}>
                      {connected
                        ? statusLabel(current.presence)
                        : `Last known: ${statusLabel(current.presence)}`}
                    </span>
                    {current.pause_reason && (
                      <p>{current.pause_reason.replaceAll("_", " ")}</p>
                    )}
                  </div>
                  <dl className="facts">
                    <dt>Resident ID</dt>
                    <dd className="num">{current.id}</dd>
                    <dt>Daily spending limit</dt>
                    <dd>
                      <span className="num">
                        ${(current.daily_limit / 1e6).toFixed(2)}
                      </span>{" "}
                      · API-equivalent estimate
                    </dd>
                    <dt>Budget timezone</dt>
                    <dd>{current.budget_timezone ?? "UTC"}</dd>
                    <dt>Runtime</dt>
                    <dd>
                      {runtimeLabel(
                        snapshot.runtimes,
                        current.profile?.execution_profile,
                      )}
                      {current.profile?.execution_profile ===
                      snapshot.runtimes.default
                        ? " · the household default"
                        : ""}
                    </dd>
                    <dt>Declaration</dt>
                    <dd>Revision {current.revision}</dd>
                    <dt>Memory</dt>
                    <dd>Revision {current.memory_revision ?? 0}</dd>
                  </dl>
                </section>
              </>
            )}
            {view === "resident" && !current && (
              <a className="back-link" href="#residents">
                ← All residents
              </a>
            )}
            {((view === "resident" && current) || view === "tasks") && (
              <div
                className={`workspace ${view === "tasks" ? "tasks-only" : ""}`}
              >
                {view === "resident" && current && (
                  <section className="work-panel">
                    <h2>Assign work</h2>
                    <p>
                      A read-only assignment. Results appear beside this panel.
                    </p>
                    <form onSubmit={submit}>
                      {pending.current &&
                        pending.current.body.resident_id !== residentId && (
                          <p className="notice" role="status">
                            Pending submission belongs to{" "}
                            {pending.current.body.resident_id}.{" "}
                            <a
                              href={`#residents/${encodeURIComponent(pending.current.body.resident_id)}`}
                            >
                              Return to that resident to retry.
                            </a>
                          </p>
                        )}
                      <label htmlFor="instruction">The assignment</label>
                      <textarea
                        id="instruction"
                        value={instruction}
                        disabled={busy || !!pending.current}
                        onChange={(e) => setInstruction(e.target.value)}
                        required
                        maxLength={32000}
                      />
                      <button
                        className="primary"
                        disabled={
                          busy ||
                          !!snapshot.restore_hold ||
                          (!pending.current &&
                            !!current.lifecycle &&
                            current.lifecycle.state !== "ready") ||
                          (!!pending.current &&
                            pending.current.body.resident_id !== residentId)
                        }
                      >
                        {pending.current
                          ? "Retry pending submission"
                          : "Run summary"}{" "}
                        <span>↗</span>
                      </button>
                      <small>
                        Uses your{" "}
                        {runtimeLabel(
                          snapshot.runtimes,
                          current.profile?.execution_profile,
                        )}
                        . Dollar amounts are API-equivalent estimates.
                      </small>
                    </form>
                    <small>
                      Pausing blocks new runs. Existing work continues until
                      explicitly cancelled; safety holds remain enforced.
                    </small>
                  </section>
                )}
                <section className="task-panel">
                  <h2>Tasks &amp; results</h2>
                  <UsageByOrigin client={client} busy={busy} act={act} />
                  {!visibleTasks.length ? (
                    <div className="empty">
                      <h3>A quiet beginning.</h3>
                      <p>
                        Once you assign something, its progress and result will
                        appear here.
                      </p>
                    </div>
                  ) : (
                    <ul className="tasks">
                      {visibleTasks.map((task) => {
                        const run = snapshot.runs.find(
                          (r) => r.task_id === task.id,
                        );
                        return (
                          <li
                            key={task.id}
                            id={run ? `run-${run.id}` : undefined}
                          >
                            <div className="task-meta">
                              <span className={`state state-${task.status}`}>
                                {statusLabel(task.status)}
                              </span>
                              <time>{clock(task.created_at)}</time>
                            </div>
                            <h3>{task.instruction}</h3>
                            {task.lineage && <Lineage hops={task.lineage} />}
                            {run?.memory_revision !== undefined && (
                              <small>
                                {run.memory_revision === 0
                                  ? "Admitted without memory"
                                  : `Memory revision ${run.memory_revision}`}
                                {run.journal_opened?.length
                                  ? ` · opened with journal ${run.journal_opened
                                      .map((sequence) => `#${sequence}`)
                                      .join(", ")}`
                                  : " · opened with no journal entries"}
                              </small>
                            )}
                            {run && (
                              <small aria-label="What the run wrote">
                                {run.memory_written?.length
                                  ? `Wrote memory revision ${run.memory_written.join(", ")}`
                                  : "Wrote no memory"}
                                {run.journal_written
                                  ? ` · wrote journal entry #${run.journal_written}`
                                  : " · wrote no journal entry"}
                              </small>
                            )}
                            {run && <RunInputs run={run} />}
                            {run?.management && (
                              <p aria-label="Management authority used by run">
                                Management grant revision{" "}
                                {run.management.grant_revision} ·{" "}
                                {run.management.calls} recorded tool calls
                              </p>
                            )}
                            {!!run?.letters_refused?.length && (
                              <div aria-label="Letters this run was refused">
                                <small>
                                  Letters refused · nothing was written
                                </small>
                                <ul>
                                  {/* Two letters can be refused in the same second for
                                      the same reason, so the position in the run's own
                                      evidence is what tells them apart. */}
                                  {run.letters_refused.map((refusal, place) => (
                                    <li key={place}>
                                      {refusal.reason.replaceAll("_", " ")}
                                      {Object.entries(refusal.details).map(
                                        ([key, value]) => (
                                          <small key={key}>
                                            {key.replaceAll("_", " ")}:{" "}
                                            {typeof value === "object"
                                              ? JSON.stringify(value)
                                              : String(value)}
                                          </small>
                                        ),
                                      )}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            {run?.skills_error && (
                              <p className="notice error">
                                Skill provenance unavailable:{" "}
                                {run.skills_error.replaceAll("_", " ")}.
                              </p>
                            )}
                            {!!run?.skills?.length && (
                              <div aria-label="Skills used by run">
                                <small>Skills used · in order</small>
                                <ol>
                                  {run.skills.map((skill) => (
                                    <li key={skill.skill_id}>
                                      <a
                                        href={`#skills/${encodeURIComponent(skill.skill_id)}`}
                                      >
                                        {skill.name}
                                      </a>{" "}
                                      · revision {skill.revision}
                                    </li>
                                  ))}
                                </ol>
                              </div>
                            )}
                            <div className="task-actions">
                              {task.status === "queued" && (
                                <button
                                  disabled={busy}
                                  onClick={() =>
                                    void act(() => client.start(task.id))
                                  }
                                >
                                  Start task ↗
                                </button>
                              )}
                              {run &&
                                ["starting", "running", "interrupted"].includes(
                                  run.status,
                                ) && (
                                  <button
                                    disabled={
                                      busy || !!run.cancellation_requested
                                    }
                                    onClick={() =>
                                      void act(() => client.cancel(run.id))
                                    }
                                  >
                                    Cancel run
                                  </button>
                                )}
                              {run?.artifact_id && (
                                <button
                                  className="result-link"
                                  onClick={() =>
                                    void act(async () => {
                                      setOutput(null);
                                      const result = await client.artifact(
                                        run.artifact_id!,
                                      );
                                      if (currentSession.current === client)
                                        setOutput({
                                          content: result.content,
                                          residentId: task.resident_id,
                                          runtimeKind: run.runtime_kind,
                                        });
                                    })
                                  }
                                >
                                  Read summary ↗
                                </button>
                              )}
                              {run && (
                                <small>
                                  {run.usage_known
                                    ? `${((run.actual_cost ?? 0) / 1e6).toFixed(4)} ${run.usage_source?.startsWith("api_equivalent") ? "API-equivalent " : ""}USD${run.usage_source === "operator_reported" ? " · operator reported" : ""}`
                                    : "Usage not yet known"}
                                </small>
                              )}
                              {run && (
                                <small aria-label="Runtime this run was worked by">
                                  {runtimeLabel(
                                    snapshot.runtimes,
                                    run.runtime_kind,
                                  )}
                                  {run.model ? ` · ${run.model}` : ""}
                                  {run.price_schedule
                                    ? ` · ${run.price_schedule}`
                                    : ""}
                                </small>
                              )}
                            </div>
                            {run &&
                              !run.usage_known &&
                              ["succeeded", "failed", "cancelled"].includes(
                                run.status,
                              ) && (
                                <UsageReport
                                  client={client}
                                  runId={run.id}
                                  busy={busy || !!snapshot.restore_hold}
                                  act={act}
                                />
                              )}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              </div>
            )}
            {view === "routines" && (
              <RoutinePanel
                client={client}
                snapshot={snapshot}
                busy={busy}
                act={act}
              />
            )}
            {output &&
              (view === "tasks" ||
                (view === "resident" && output.residentId === residentId)) && (
                <SummaryOutput
                  key={output.content}
                  content={output.content}
                  eyebrow={resultEyebrow(snapshot.runtimes, output.runtimeKind)}
                  onClose={() => setOutput(null)}
                />
              )}
            {view === "resident" && current && (
              <div key={`profile:${current.id}`}>
                <ResidentMaintenance
                  key={`maintenance:${snapshot.epoch}:${current.id}`}
                  client={client}
                  resident={current}
                  readOnly={snapshot.restore_hold === true}
                  routines={snapshot.routines ?? []}
                  onChanged={() => void act(async () => {})}
                />
                <MemoryHistory
                  key={`memory:${snapshot.epoch}:${current.id}`}
                  client={client}
                  resident={current}
                  busy={busy}
                  act={act}
                  openable={openableRun}
                />
                <Journal
                  key={`journal:${snapshot.epoch}:${current.id}`}
                  client={client}
                  resident={current}
                  busy={busy}
                  act={act}
                  openable={openableRun}
                />
                {/* Keyed on the resident alone, unlike its neighbours above: the
                    others hold only server data a remount refetches, while Letters
                    holds an unsent draft and the frozen identity of a command whose
                    answer never arrived. Dropping those on a store swap would hand the
                    operator a fresh command id for a letter Hearth may already hold.
                    Its list is read on demand and reloaded by the same button. */}
                <Letters
                  key={`letters:${current.id}`}
                  client={client}
                  resident={current}
                  residents={residents}
                  busy={busy}
                  readOnly={snapshot.restore_hold === true}
                  act={act}
                  openable={openableRun}
                />
                {current.profile && (
                  <ProfileProvenance
                    profile={current.profile}
                    runtimes={snapshot.runtimes}
                  />
                )}
                {current.management && (
                  <section
                    className="management-profile"
                    aria-label="Resident management authority"
                  >
                    <h3>Management authority</h3>
                    {"error" in current.management ? (
                      <p role="alert">
                        {current.management.error.replaceAll("_", " ")}
                      </p>
                    ) : (
                      <p>
                        {current.management.enabled
                          ? `Enabled · grant revision ${current.management.revision} · up to ${current.management.max_residents} managed residents`
                          : "No management tools granted."}
                      </p>
                    )}
                    <a href={`#management/${current.id}`}>
                      Inspect or edit the operator grant →
                    </a>
                  </section>
                )}
              </div>
            )}

            {view === "inbox" && (
              <section className="output inbox" aria-label="Inbox">
                <div className="section-title">
                  <div>
                    <h2>Inbox</h2>
                  </div>
                  <span className="eyebrow">
                    {unread} unread · showing {notifications.length}
                  </span>
                </div>
                <p>
                  Everything Hearth raises is recorded here and stays here.
                  Reading one only marks it read.
                </p>
                {!notifications.length && (
                  <p className="muted">Run results will appear here.</p>
                )}
                <ul className="tasks">
                  {notifications.map((n) => (
                    <li
                      key={n.id}
                      className={n.read_at === null ? "unread" : ""}
                    >
                      <h3>
                        {n.kind.replace("run.", "Run ")}
                        {n.read_at === null && (
                          <span className="nav-badge">New</span>
                        )}
                      </h3>
                      <p>
                        <time>{clock(n.created_at)}</time>
                      </p>
                      <a
                        href={`/#run-${encodeURIComponent(n.resource_id)}`}
                        onClick={() => setView("tasks")}
                        aria-label={`Open the run ${n.resource_id} and its result`}
                      >
                        Open run and result
                      </a>
                      <button
                        className="quiet"
                        disabled={busy}
                        aria-label={`Mark the ${n.kind.replace("run.", "run ")} notice for ${n.resource_id} ${n.read_at === null ? "read" : "unread"}`}
                        onClick={() =>
                          act(() =>
                            client.markNotification(n.id, n.read_at === null),
                          )
                        }
                      >
                        {n.read_at === null ? "Mark read" : "Mark unread"}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {(view === "activity" || view === "townhall") && (
              <section className="activity" aria-label="Recent activity">
                <h2>Recent activity</h2>
                {snapshot.activity
                  .slice(0, view === "activity" ? 50 : 8)
                  .map((item) => (
                    <div key={item.sequence}>
                      <time>{clock(item.at)}</time>
                      <span>
                        {item.kind.replaceAll(".", " · ").replaceAll("_", " ")}
                      </span>
                      <span className="audit-sequence">#{item.sequence}</span>
                    </div>
                  ))}
                {!snapshot.activity.length && (
                  <p className="muted">Nothing has happened yet.</p>
                )}
              </section>
            )}
          </>
        )}
        <footer>
          <span>Hearth · local development</span>
          <span>Fictional notes. Read-only summaries.</span>
        </footer>
      </main>
    </>
  );
}
