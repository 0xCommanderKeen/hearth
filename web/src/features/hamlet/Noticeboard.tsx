import { useState } from "react";
import type { Snapshot } from "../../shared/client";

export function Noticeboard({
  snapshot,
  connected,
}: {
  snapshot: Snapshot;
  connected: boolean;
}) {
  const [showAllAttention, setShowAllAttention] = useState(false);
  const name = (id: string | null) =>
    id === null
      ? "Townhall"
      : (snapshot.residents.find((r) => r.id === id)?.name ?? id);
  const runs = snapshot.runs ?? [];
  const completed = runs
    .filter((r) => r.status === "succeeded")
    .sort(
      (a, b) =>
        (b.finished_at ?? 0) - (a.finished_at ?? 0) || a.id.localeCompare(b.id),
    );
  // A failed historical attempt is not a new alert after a successful retry.
  const attention = (snapshot.tasks ?? []).filter((t) =>
    ["failed", "interrupted", "stopping"].includes(t.status),
  );
  const holds = snapshot.residents.filter((r) => (r.unresolved_runs ?? 0) > 0);
  const letters = [...(snapshot.letters ?? [])].sort((a, b) => b.at - a.at);
  const taskText = (taskId: string) => {
    const task = snapshot.tasks?.find((t) => t.id === taskId);
    return task
      ? task.instruction + (task.instruction_truncated ? "…" : "")
      : "Task details are outside this snapshot.";
  };
  const time = (at?: number | null) =>
    at != null && (
      <time dateTime={new Date(at * 1000).toISOString()}>
        {new Date(at * 1000).toLocaleString()}
      </time>
    );
  return (
    <section className="village-noticeboard" aria-label="Village noticeboard">
      <div className="section-title">
        <h2>Village noticeboard</h2>
        <a href="#activity">All activity →</a>
      </div>
      <p>
        {connected
          ? "From the latest village records."
          : "Disconnected · showing last known records."}{" "}
        Showing recent items from the available snapshot. Expand attention items
        to see all retained tasks and holds.
      </p>
      <div className="noticeboard-columns">
        <section aria-label="Work needing attention">
          <h3>
            Needs attention <small>· {attention.length + holds.length}</small>
          </h3>
          {!attention.length && !holds.length && (
            <p>No work needing attention in these records.</p>
          )}
          <ul>
            {holds.slice(0, showAllAttention ? undefined : 4).map((r) => (
              <li key={`hold:${r.id}`}>
                <a href={`#residents/${encodeURIComponent(r.id)}?panel=work`}>
                  {r.name} · {r.unresolved_runs} unresolved run(s)
                </a>
                <p>
                  {r.lifecycle?.state === "archived"
                    ? "Archived resident. "
                    : ""}
                  Accounting holds remain.
                </p>
              </li>
            ))}
            {attention
              .slice(
                0,
                showAllAttention ? undefined : Math.max(0, 4 - holds.length),
              )
              .map((task) => (
                <li key={`task:${task.id}`}>
                  <a
                    href={`#residents/${encodeURIComponent(task.resident_id)}?panel=work`}
                  >
                    {name(task.resident_id)} ·{" "}
                    {task.status === "interrupted"
                      ? "Outcome unknown"
                      : task.status === "stopping"
                        ? "Stopping · outcome pending"
                        : "Task failed"}
                  </a>
                  <p>{taskText(task.id)}</p>
                </li>
              ))}
          </ul>
          {attention.length + holds.length > 4 && (
            <button
              onClick={() => setShowAllAttention(!showAllAttention)}
              aria-expanded={showAllAttention}
            >
              {showAllAttention
                ? "Show fewer attention items"
                : `Show all ${attention.length + holds.length} attention items`}
            </button>
          )}
          <a href="#tasks">Inspect tasks →</a>
        </section>
        <section aria-label="Recently completed work">
          <h3>
            Completed runs <small>· {completed.length}</small>
          </h3>
          {!completed.length && <p>No completed runs in these records.</p>}
          <ul>
            {completed.slice(0, 4).map((run) => (
              <li key={run.id}>
                <a href={`#runs/${encodeURIComponent(run.id)}`}>
                  {name(run.resident_id)} · Run completed
                </a>
                <p>{taskText(run.task_id)}</p>
                {time(run.finished_at)}
              </li>
            ))}
          </ul>
          <a href="#tasks">Tasks & results →</a>
        </section>
        <section aria-label="Letters on the noticeboard">
          <h3>
            Recent letters <small>· {letters.length}</small>
          </h3>
          {!letters.length && <p>No letters in these records.</p>}
          <ul>
            {letters.slice(0, 4).map((event) => (
              <li key={`${event.kind}:${event.task_id}`}>
                <a
                  href={
                    event.to_resident_id === null
                      ? "#inbox"
                      : `#residents/${encodeURIComponent(event.to_resident_id)}?panel=letters`
                  }
                >
                  {name(event.from_resident_id)} → {name(event.to_resident_id)}
                </a>
                <p>
                  {event.kind === "letter_sent" ? "Sent" : "Answered"} ·{" "}
                  {event.title}
                </p>
                {time(event.at)}
              </li>
            ))}
          </ul>
          <a href="#inbox">Open Inbox →</a>
        </section>
      </div>
    </section>
  );
}
