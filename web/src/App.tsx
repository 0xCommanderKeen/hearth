import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Client,
  RequestError,
  StateFormatError,
  type PendingTask,
  type Snapshot,
} from "./client";
import "./style.css";
import { Approvals } from "./Approvals";
import { RoutinePanel } from "./Routines";
import { UsageReport } from "./UsageReport";
import { Skills } from "./Skills";
import { Memory } from "./Memory";
import { Hamlet } from "./Hamlet";

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
  simulated,
  onClose,
}: {
  content: string;
  simulated: boolean;
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
        <span className="eyebrow">
          {simulated ? "SIMULATED ARTIFACT" : "CODEX RESULT"}
        </span>
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
  | "tasks"
  | "routines"
  | "approvals"
  | "activity"
  | "hamlet";

export function App() {
  const [client, setClient] = useState<Client | null>(null);
  const [token, setToken] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [view, setView] = useState<Page>("townhall");
  const [residentId, setResidentId] = useState("reader");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [instruction, setInstruction] = useState(
    "Summarize today’s synthetic notes.",
  );
  const [linkedApproval, setLinkedApproval] = useState<string | null>(null);
  const [output, setOutput] = useState<{
    content: string;
    residentId?: string;
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
  async function login(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const candidate = new Client(token);
    currentSession.current = candidate;
    try {
      const next = await candidate.state();
      if (currentSession.current !== candidate) return;
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
        hash = decodeURIComponent(window.location.hash);
      } catch {
        setError("Invalid notification link");
        return;
      }
      if (hash.startsWith("#residents/")) {
        setResidentId(hash.slice(11));
        setView("resident");
      } else if (
        [
          "#townhall",
          "#hamlet",
          "#residents",
          "#tasks",
          "#routines",
          "#approvals",
          "#activity",
        ].includes(hash)
      ) {
        setView(hash.slice(1) as Page);
      }
      if (hash.startsWith("#approval-")) {
        setView("approvals");
        setLinkedApproval(hash.slice(10));
      }
      if (hash.startsWith("#run-") && client) {
        setView("tasks");
        const id = hash.slice(5);
        void act(async () => {
          const run = await client.run(id);
          const content = run.artifact_id
            ? (await client.artifact(run.artifact_id)).content
            : `Run ${run.status}`;
          if (currentSession.current === client) setOutput({ content });
        });
      }
    };
    openLinkedView();
    window.addEventListener("hashchange", openLinkedView);
    return () => window.removeEventListener("hashchange", openLinkedView);
  }, [client]);
  useEffect(() => {
    const hash = window.location.hash;
    if (hash.startsWith("#approval-") || hash.startsWith("#run-"))
      document
        .getElementById(hash.slice(1))
        ?.scrollIntoView?.({ block: "center" });
  }, [view, snapshot]);
  const residents = snapshot?.residents ?? [];
  const visibleTasks = (snapshot?.tasks ?? []).filter(
    (task) => view !== "resident" || task.resident_id === residentId,
  );
  const completed =
    snapshot?.runs.filter((r) => r.status === "succeeded").length ?? 0;
  const active =
    snapshot?.runs.filter((r) =>
      ["starting", "running", "stopping", "interrupted"].includes(r.status),
    ).length ?? 0;

  return (
    <>
      <header className="topbar">
        <a className="brand" href="#townhall" aria-label="Hearth home">
          <Emblem />
          <span>
            hearth<span className="brand-note">RESIDENT CONTROL PANEL</span>
          </span>
        </a>
        <nav aria-label="Main views">
          {[
            "townhall",
            "residents",
            "tasks",
            "routines",
            "approvals",
            "activity",
            "hamlet",
          ].map((page, index) => (
            <a
              key={page}
              href={`#${page}`}
              className={
                view === page || (page === "residents" && view === "resident")
                  ? "selected"
                  : ""
              }
              aria-current={
                view === page || (page === "residents" && view === "resident")
                  ? "page"
                  : undefined
              }
            >
              <span className="nav-number">0{index + 1}</span>
              {page === "townhall"
                ? "Townhall"
                : page[0].toUpperCase() + page.slice(1)}
            </a>
          ))}
        </nav>
        <span className="mode">
          {snapshot
            ? snapshot.simulated
              ? "✳ SIMULATION"
              : "CODEX · SUBSCRIPTION"
            : "HEARTH"}
        </span>
        {client && (
          <button className="quiet" onClick={lock}>
            Lock
          </button>
        )}
      </header>
      <main>
        <div className="intro">
          <div>
            <span className="eyebrow">
              HEARTH / {view === "hamlet" ? "VILLAGE" : "TOWNHALL"}
            </span>
            <h1>
              {view === "resident"
                ? (residents.find((r) => r.id === residentId)?.name ??
                  "Resident not found")
                : view === "townhall"
                  ? "Townhall"
                  : view[0].toUpperCase() + view.slice(1)}
            </h1>
            <p className="muted">
              {view === "townhall"
                ? "Residents, their work, and what needs your attention."
                : view === "residents"
                  ? "Everyone who lives here. Select a resident to see their purpose and work."
                  : view === "resident"
                    ? "Purpose, controls, memory and recent work."
                    : view === "hamlet"
                      ? "Your residents at home."
                      : "Work and activity recorded by Hearth."}
            </p>
          </div>
        </div>
        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
        {!client || !snapshot ? (
          <section className="entry">
            <div>
              <span className="eyebrow">WELCOME HOME</span>
              <h2>Open the gate.</h2>
              <p>
                Enter your local operator token to explore the simulation. It
                stays in memory for this visit.
              </p>
            </div>
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
            <div className="status-strip">
              <span>
                <i className={`dot ${connected ? "working" : ""}`} />
                {connected
                  ? snapshot.simulated
                    ? "Connected to the simulation"
                    : "Connected to Codex"
                  : "Reconnecting · displayed state may be stale"}
              </span>
              <span>
                {residents.length} resident · {active} active · {completed}{" "}
                completed in recent history
              </span>
            </div>
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
                {view === "townhall" && (
                  <div className="overview-stats">
                    <div>
                      <strong>{residents.length}</strong>
                      <span>Residents</span>
                    </div>
                    <div>
                      <strong>{active}</strong>
                      <span>Active runs</span>
                    </div>
                    <div>
                      <strong>{completed}</strong>
                      <span>Recent completed runs</span>
                    </div>
                  </div>
                )}
                <div className="section-title">
                  <h2>Residents</h2>
                </div>
                {!residents.length && (
                  <div className="empty">
                    <p>No residents yet. Reader summarizes synthetic notes.</p>
                    <button
                      className="primary"
                      disabled={busy || snapshot.restore_hold}
                      onClick={() => void act(() => client.seed())}
                    >
                      {snapshot.simulated
                        ? "Set up mock Reader"
                        : "Set up Reader"}
                    </button>
                  </div>
                )}
                {residents.length > 0 && (
                  <div className="resident-table">
                    <div className="resident-table-head">
                      <span>Resident / purpose</span>
                      <span>Status</span>
                      <span>Daily limit</span>
                      <span>Profile</span>
                    </div>
                    {residents.map((r) => (
                      <a
                        className="resident-row"
                        key={r.id}
                        href={`#residents/${encodeURIComponent(r.id)}`}
                      >
                        <div>
                          <strong>{r.name}</strong>
                          <small>{r.id}</small>
                          <p>{r.purpose}</p>
                        </div>
                        <span className={`state state-${r.presence}`}>
                          {connected
                            ? statusLabel(r.presence)
                            : "Last known: " + statusLabel(r.presence)}
                        </span>
                        <div>
                          ${(r.daily_limit / 1e6).toFixed(2)}
                          <small>{r.budget_timezone ?? "UTC"}</small>
                        </div>
                        <span className="profile-link">View resident →</span>
                      </a>
                    ))}
                  </div>
                )}
              </section>
            )}
            {view === "resident" && (
              <a className="back-link" href="#residents">
                ← All residents
              </a>
            )}
            {view === "resident" &&
              residents
                .filter((r) => r.id === residentId)
                .map((r) => (
                  <section
                    className="profile-facts"
                    key={r.id}
                    aria-label="Resident information"
                  >
                    <div>
                      <span className="eyebrow">PURPOSE</span>
                      <h2>{r.purpose}</h2>
                      <span className={`state state-${r.presence}`}>
                        {connected
                          ? statusLabel(r.presence)
                          : `Last known: ${statusLabel(r.presence)}`}
                      </span>
                      {r.pause_reason && (
                        <p>{r.pause_reason.replaceAll("_", " ")}</p>
                      )}
                    </div>
                    <dl>
                      <dt>Resident ID</dt>
                      <dd>{r.id}</dd>
                      <dt>Daily spending limit</dt>
                      <dd>
                        ${(r.daily_limit / 1e6).toFixed(2)} · API-equivalent
                        estimate
                      </dd>
                      <dt>Budget timezone</dt>
                      <dd>{r.budget_timezone ?? "UTC"}</dd>
                      <dt>Declaration</dt>
                      <dd>Revision {r.revision}</dd>
                      <dt>Memory</dt>
                      <dd>Revision {r.memory_revision ?? 0}</dd>
                    </dl>
                  </section>
                ))}
            {(view === "resident" || view === "tasks") && (
              <div
                className={`workspace ${view === "tasks" ? "tasks-only" : ""}`}
              >
                {view === "resident" &&
                  residents.some((r) => r.id === residentId) && (
                    <section className="work-panel">
                      <div className="section-title">
                        <span className="eyebrow">01 / THE DAY’S WORK</span>
                        <h2>
                          {residents.length
                            ? `Assign work to ${residents.find((r) => r.id === residentId)?.name ?? "resident"}.`
                            : "Make room for Reader."}
                        </h2>
                      </div>
                      <p className="muted">
                        {residents.length
                          ? "A read-only assignment using synthetic notes."
                          : "Your first resident summarizes synthetic notes. No model calls, real files, or external actions."}
                      </p>
                      {!residents.length ? (
                        <button
                          className="primary"
                          disabled={busy}
                          onClick={() => void act(() => client.seed())}
                        >
                          {snapshot.simulated
                            ? "Set up mock Reader"
                            : "Set up Reader"}{" "}
                          <span>＋</span>
                        </button>
                      ) : (
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
                              (!!pending.current &&
                                pending.current.body.resident_id !== residentId)
                            }
                          >
                            {pending.current
                              ? "Retry pending submission"
                              : snapshot.simulated
                                ? "Run a mock summary"
                                : "Run summary"}{" "}
                            <span>↗</span>
                          </button>
                          <small>
                            {snapshot.simulated
                              ? "Mock usage only. No money is spent."
                              : "Uses your Codex subscription. Dollar amounts are API-equivalent estimates."}
                          </small>
                        </form>
                      )}
                      {residents.length > 0 && (
                        <small>
                          Pausing blocks new runs. Existing work continues until
                          explicitly cancelled; safety holds remain enforced.
                        </small>
                      )}
                      {residents
                        .filter((r) => r.id === residentId)
                        .map((r) => (
                          <div className="resident-line" key={r.id}>
                            <span className="resident-avatar">R</span>
                            <div>
                              <strong>{r.name}</strong>
                              <span>
                                {connected
                                  ? statusLabel(r.presence)
                                  : `Last known: ${statusLabel(r.presence)}`}
                                {r.pause_reason
                                  ? ` · ${r.pause_reason.replaceAll("_", " ")}`
                                  : ""}
                              </span>
                            </div>
                            <button
                              disabled={busy || snapshot.restore_hold}
                              onClick={() =>
                                void act(() =>
                                  client.pauseResident(
                                    r.id,
                                    !r.operator_paused,
                                    r.control_revision ?? 0,
                                  ),
                                )
                              }
                            >
                              {r.operator_paused
                                ? "Resume new runs"
                                : "Pause new runs"}
                            </button>
                            <span className="revision">
                              REV {r.revision}
                              <br />
                              Budget day: {r.budget_timezone ?? "UTC"}
                            </span>
                          </div>
                        ))}
                      {residents
                        .filter((r) => r.id === residentId)
                        .map((r) => (
                          <Skills
                            key={`${snapshot.epoch}:${r.id}`}
                            client={client}
                            resident={r}
                            busy={busy}
                            readOnly={snapshot.restore_hold === true}
                            act={act}
                          />
                        ))}
                      {residents
                        .filter((r) => r.id === residentId)
                        .map((r) => (
                          <Memory
                            key={`${snapshot.epoch}:${r.id}`}
                            client={client}
                            resident={r}
                            busy={busy}
                            readOnly={snapshot.restore_hold === true}
                            act={act}
                          />
                        ))}
                    </section>
                  )}
                <section className="task-panel">
                  <div className="section-title">
                    <span className="eyebrow">02 / TASKS & RESULTS</span>
                    <h2>Tasks & results</h2>
                  </div>
                  {!visibleTasks.length ? (
                    <div className="empty">
                      <span>☷</span>
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
                            {run?.memory_revision !== undefined && (
                              <small>
                                {run.memory_revision === 0
                                  ? "Admitted without memory"
                                  : `Memory revision ${run.memory_revision}`}
                              </small>
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
                                    ? `${((run.actual_cost ?? 0) / 1e6).toFixed(4)} ${snapshot.simulated ? "simulated " : ""}${run.usage_source?.startsWith("api_equivalent") ? "API-equivalent " : ""}USD${run.usage_source === "operator_reported_mock" ? " · operator reported" : ""}`
                                    : "Usage not yet known"}
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
            {view === "approvals" && (
              <Approvals
                linkedId={linkedApproval}
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
                  simulated={snapshot.simulated}
                  onClose={() => setOutput(null)}
                />
              )}
            {(view === "activity" || view === "townhall") && (
              <section className="output" aria-label="Mock notifications">
                <span className="eyebrow">LOCAL MOCK INBOX</span>
                <h2>News from Hearth.</h2>
                <p>
                  Delivery status is separate from work status. These
                  notifications stay local and never approve an action.
                </p>
                {!snapshot.notifications?.length && (
                  <p className="muted">
                    Results and approval requests will appear here.
                  </p>
                )}
                <ul className="tasks">
                  {snapshot.notifications?.map((n) => (
                    <li key={n.id}>
                      <h3>
                        {n.kind === "approval.requested"
                          ? "A mock action needs review"
                          : n.kind.replace("run.", "Run ")}
                      </h3>
                      <p>
                        {n.status === "retry"
                          ? "Delivery unconfirmed; retry scheduled"
                          : n.status === "pending"
                            ? "Waiting for delivery confirmation"
                            : n.status === "obsolete"
                              ? "No longer current"
                              : "Delivered to the local mock inbox"}{" "}
                        · {n.attempts} attempts
                      </p>
                      {n.status !== "obsolete" && (
                        <a
                          href={`/#${n.kind === "approval.requested" ? "approval" : "run"}-${encodeURIComponent(n.resource_id)}`}
                          onClick={() => {
                            if (n.kind === "approval.requested")
                              setView("approvals");
                          }}
                        >
                          {n.kind === "approval.requested"
                            ? "Open approval review"
                            : "Open run and result"}
                        </a>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {(view === "activity" || view === "townhall") && (
              <section className="activity">
                <span className="eyebrow">RECENT ACTIVITY</span>
                {snapshot.activity.slice(0, 5).map((item) => (
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
          <span>
            Hearth <span className="muted">/</span> Local development
          </span>
          <span>Synthetic notes. Read-only summaries.</span>
        </footer>
      </main>
    </>
  );
}
