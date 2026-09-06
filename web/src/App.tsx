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

function Hamlet({
  snapshot,
  connected,
}: {
  snapshot: Snapshot;
  connected: boolean;
}) {
  const resident = snapshot.residents[0];
  const working = connected && resident?.presence === "running";
  return (
    <section className="village" aria-label="Hamlet village">
      <div className="village-heading">
        <span className="eyebrow">HAMLET / SIMULATED VILLAGE</span>
        <span className="map-label">One home. Room to grow.</span>
      </div>
      <svg
        viewBox="0 0 860 370"
        role="img"
        aria-label={
          resident
            ? `${resident.name}'s home. ${connected ? statusLabel(resident.presence) : "Connection lost; last known state shown"}.`
            : "An empty village awaiting its first resident"
        }
      >
        <defs>
          <pattern
            id="ground"
            width="23"
            height="23"
            patternUnits="userSpaceOnUse"
          >
            <circle cx="3" cy="3" r=".8" fill="#7e8c71" opacity=".28" />
          </pattern>
        </defs>
        <path d="M60 230 300 85 785 210 545 355Z" fill="#dce0c9" />
        <path d="M60 230 545 355 785 210v15L545 370 60 245Z" fill="#bec7ab" />
        <path d="M60 230 300 85 785 210 545 355Z" fill="url(#ground)" />
        <path
          d="m230 295 118-96 160 43 146-88"
          fill="none"
          stroke="#f7f1df"
          strokeWidth="25"
          strokeLinejoin="round"
        />
        <path
          d="m230 295 118-96 160 43 146-88"
          fill="none"
          stroke="#d5c9a8"
          strokeWidth="1"
          strokeDasharray="3 7"
        />
        <g transform="translate(482 96)">
          <path d="m0 81 77-40 93 29-77 42Z" fill="#a8b495" opacity=".4" />
          <path d="m12 17 62 20v61L12 77Z" fill="#d5bd96" />
          <path d="m74 37 57-30v62L74 98Z" fill="#e9d6b2" />
          <path d="m0 20 55-48 87 33-68 37Z" fill="#586d57" />
          <path d="m55-28 87 33-10 3-57-24Z" fill="#3c5140" />
          <path d="m93 56 18-9v32l-18 9Z" fill="#624e37" />
          <path d="m24 44 18 6v17l-18-6Z" fill="#6f7f73" />
          <path d="m111 27 12-6v13l-12 6Z" fill="#6f7f73" />
        </g>
        <text x="582" y="220" className="svg-label">
          TOWNHALL
        </text>
        <g transform="translate(276 142)" opacity={resident ? 1 : 0.4}>
          <ellipse
            cx="14"
            cy="76"
            rx="79"
            ry="24"
            fill="#8b997d"
            opacity=".25"
          />
          <path d="m-47 4 58 21v70l-58-22Z" fill="#dfc49b" />
          <path d="m11 25 57-33v70L11 95Z" fill="#f1ddb5" />
          <path d="m-60 7 49-54 91 30L10 31Z" fill="#aa533b" />
          <path d="m-11-54 91 30-12 16-57-34Z" fill="#813c2c" />
          <path d="m-33-35 14 4v-29l-14-4Z" fill="#9d765e" />
          <path d="m-19-31 9-6v-29l-9 6Z" fill="#bc9471" />
          <path
            d="m-32 32 20 7v22l-20-7Z"
            fill={working ? "#f2b954" : "#788878"}
          />
          <path d="m-22 36 0 21m-10-14 20 7" stroke="#b6a480" strokeWidth="2" />
          <path d="m29 40 20-12v40L29 80Z" fill="#6d7356" />
          <path
            d="m57 22 7-4v13l-7 4Z"
            fill={working ? "#f2b954" : "#788878"}
          />
          <circle cx="43" cy="55" r="2" fill="#e8d29b" />
          {resident && (
            <g transform="translate(81 79)">
              <ellipse cy="17" rx="12" ry="5" fill="#8b997d" opacity=".5" />
              <path d="m-7 0 14 0 3 16h-20Z" fill="#a65337" />
              <circle cy="-7" r="7" fill="#d4ac81" />
              <path d="M-8-9q0-12 15-2" fill="#524630" />
            </g>
          )}
        </g>
        <text x="284" y="282" textAnchor="middle" className="svg-label">
          {resident?.name.toUpperCase() ?? "YOUR FIRST RESIDENT"}
        </text>
        {[
          [153, 201],
          [701, 247],
          [615, 280],
          [388, 104],
        ].map(([x, y], i) => (
          <g key={i} transform={`translate(${x} ${y})`}>
            <ellipse cy="31" rx="20" ry="7" fill="#8b997d" opacity=".3" />
            <path d="M-3 5h6v27h-6Z" fill="#8b7955" />
            <path d="M0-33-23 15h46Z" fill={i % 2 ? "#7d9068" : "#637e60"} />
            <path d="M0-13-27 27h54Z" fill="#7d9068" />
          </g>
        ))}
      </svg>
      <div className="village-foot">
        <span className={`dot ${working ? "working" : ""}`} />
        <span>
          {resident
            ? `${resident.name} · ${connected ? statusLabel(resident.presence) : "Last known state — disconnected"}`
            : "No residents declared yet"}
        </span>
        <span className="legend">Every visible state comes from Hearth.</span>
      </div>
    </section>
  );
}

export function App() {
  const [client, setClient] = useState<Client | null>(null);
  const [token, setToken] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [view, setView] = useState<"hamlet" | "townhall">("hamlet");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [instruction, setInstruction] = useState(
    "Summarize today’s synthetic notes.",
  );
  const [linkedApproval, setLinkedApproval] = useState<string | null>(null);
  const [output, setOutput] = useState<string | null>(null);
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
          resident_id: "reader",
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
      if (hash.startsWith("#approval-")) {
        setView("townhall");
        setLinkedApproval(hash.slice(10));
      }
      if (hash.startsWith("#run-") && client) {
        const id = hash.slice(5);
        void act(async () => {
          const run = await client.run(id);
          const content = run.artifact_id
            ? (await client.artifact(run.artifact_id)).content
            : `Run ${run.status}`;
          if (currentSession.current === client) setOutput(content);
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
  const completed =
    snapshot?.runs.filter((r) => r.status === "succeeded").length ?? 0;
  const active =
    snapshot?.runs.filter((r) =>
      ["starting", "running", "stopping", "interrupted"].includes(r.status),
    ).length ?? 0;

  return (
    <>
      <header className="topbar">
        <a className="brand" href="#" aria-label="Hearth home">
          <Emblem />
          <span>
            hearth<span className="brand-note">A HOME FOR USEFUL WORK</span>
          </span>
        </a>
        <nav aria-label="Main views">
          <button
            className={view === "hamlet" ? "selected" : ""}
            aria-pressed={view === "hamlet"}
            onClick={() => setView("hamlet")}
          >
            Hamlet
          </button>
          <button
            className={view === "townhall" ? "selected" : ""}
            aria-pressed={view === "townhall"}
            onClick={() => setView("townhall")}
          >
            Townhall
          </button>
        </nav>
        <span className="mode">✳ SIMULATION</span>
        {client && (
          <button className="quiet" onClick={lock}>
            Lock
          </button>
        )}
      </header>
      <main>
        <div className="intro">
          <div>
            <span className="eyebrow">HEARTH / EARLY DAYS</span>
            <h1>
              {view === "hamlet" ? (
                <>
                  A little village.
                  <br />
                  <em>Useful work.</em>
                </>
              ) : (
                <>
                  Good work starts
                  <br />
                  with <em>a clear request.</em>
                </>
              )}
            </h1>
          </div>
          <p>
            A home for residents with purpose,
            <br />
            memory, and a little work to do.
            <br />
            <span>This village is a working simulation.</span>
          </p>
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
                  ? "Connected to the simulation"
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
            <div className="workspace">
              <section className="work-panel">
                <div className="section-title">
                  <span className="eyebrow">01 / THE DAY’S WORK</span>
                  <h2>
                    {residents.length
                      ? "Something for Reader."
                      : "Make room for Reader."}
                  </h2>
                </div>
                <p className="muted">
                  {residents.length
                    ? "A small, read-only assignment. The mock returns a fixed summary from synthetic notes."
                    : "Your first resident summarizes synthetic notes. No model calls, real files, or external actions."}
                </p>
                {!residents.length ? (
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() => void act(() => client.seed())}
                  >
                    Set up mock Reader <span>＋</span>
                  </button>
                ) : (
                  <form onSubmit={submit}>
                    <label htmlFor="instruction">The assignment</label>
                    <textarea
                      id="instruction"
                      value={instruction}
                      disabled={busy || !!pending.current}
                      onChange={(e) => setInstruction(e.target.value)}
                      required
                      maxLength={32000}
                    />
                    <button className="primary" disabled={busy}>
                      {pending.current
                        ? "Retry pending submission"
                        : "Run a mock summary"}{" "}
                      <span>↗</span>
                    </button>
                    <small>Mock usage only. No money is spent.</small>
                  </form>
                )}
                {residents.map((r) => (
                  <div className="resident-line" key={r.id}>
                    <span className="resident-avatar">R</span>
                    <div>
                      <strong>{r.name}</strong>
                      <span>
                        {statusLabel(r.presence)}
                        {r.pause_reason
                          ? ` · ${r.pause_reason.replaceAll("_", " ")}`
                          : ""}
                      </span>
                    </div>
                    <span className="revision">REV {r.revision}</span>
                  </div>
                ))}
              </section>
              <section className="task-panel">
                <div className="section-title">
                  <span className="eyebrow">02 / TASKS & RESULTS</span>
                  <h2>A record of real steps.</h2>
                </div>
                {!snapshot.tasks.length ? (
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
                    {snapshot.tasks.map((task) => {
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
                                    const result = await client.artifact(
                                      run.artifact_id!,
                                    );
                                    if (currentSession.current === client)
                                      setOutput(result.content);
                                  })
                                }
                              >
                                Read summary ↗
                              </button>
                            )}
                            {run && (
                              <small>
                                {run.usage_known
                                  ? `${((run.actual_cost ?? 0) / 1e6).toFixed(4)} simulated USD`
                                  : "Usage not yet known"}
                              </small>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
            </div>
            {view === "townhall" && (
              <RoutinePanel
                client={client}
                snapshot={snapshot}
                busy={busy}
                act={act}
              />
            )}
            {view === "townhall" && (
              <Approvals
                linkedId={linkedApproval}
                client={client}
                snapshot={snapshot}
                busy={busy}
                act={act}
              />
            )}
            {output && (
              <section className="output" aria-label="Summary output">
                <div className="section-title">
                  <span className="eyebrow">SIMULATED ARTIFACT</span>
                  <button className="quiet" onClick={() => setOutput(null)}>
                    Close ×
                  </button>
                </div>
                <pre>{output}</pre>
              </section>
            )}
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
                            setView("townhall");
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
          </>
        )}
        <footer>
          <span>
            Hearth <span className="muted">/</span> Early days, carefully built.
          </span>
          <span>Synthetic notes. Simulated residents. Honest state.</span>
        </footer>
      </main>
    </>
  );
}
