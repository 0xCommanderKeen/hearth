import type { ReactNode } from "react";
import { Client, type Resident, type Snapshot } from "../../shared/client";
import { runtimeLabel } from "../../shared/runtimes";
import { ResidentActivityLog } from "./Activity";
import "./resident-page.css";

export const residentTabs = [
  "Overview",
  "Tasks",
  "Activity",
  "Memory",
  "Skills",
  "Access",
  "Settings",
] as const;
export type ResidentTab = (typeof residentTabs)[number];
export function routeTab(hash: string): ResidentTab {
  const query = new URLSearchParams(hash.split("?")[1]);
  const legacy = query.get("panel");
  if (legacy === "journal") return "Memory";
  if (legacy === "letters" || legacy === "work") return "Tasks";
  return (
    residentTabs.find((tab) => tab.toLowerCase() === query.get("tab")) ??
    "Overview"
  );
}
export function ResidentTabs({
  active,
  onSelect,
}: {
  active: ResidentTab;
  onSelect: (tab: ResidentTab) => void;
}) {
  return (
    <nav className="resident-tabs" aria-label="Resident details" role="tablist">
      {residentTabs.map((tab, index) => (
        <button
          key={tab}
          id={`resident-tab-${tab}`}
          role="tab"
          aria-selected={tab === active}
          aria-controls={`resident-panel-${tab}`}
          tabIndex={tab === active ? 0 : -1}
          onClick={() => onSelect(tab)}
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
              return;
            event.preventDefault();
            const next =
              event.key === "Home"
                ? 0
                : event.key === "End"
                  ? residentTabs.length - 1
                  : (index +
                      (event.key === "ArrowRight" ? 1 : -1) +
                      residentTabs.length) %
                    residentTabs.length;
            onSelect(residentTabs[next]);
            document
              .getElementById(`resident-tab-${residentTabs[next]}`)
              ?.focus();
          }}
        >
          {tab}
        </button>
      ))}
    </nav>
  );
}
export function ResidentPanel({
  name,
  active,
  children,
}: {
  name: ResidentTab;
  active: ResidentTab;
  children: ReactNode;
}) {
  return (
    <div
      className="resident-tab-panel"
      id={`resident-panel-${name}`}
      role="tabpanel"
      aria-labelledby={`resident-tab-${name}`}
      hidden={name !== active}
    >
      {children}
    </div>
  );
}
export const presenceLabel = (state: string) =>
  ({
    ready: "Ready for work",
    running: "Working",
    starting: "Starting",
    stopping: "Stopping",
    interrupted: "Outcome unknown",
    paused: "Paused",
    succeeded: "Completed",
    failed: "Failed",
    queued: "Queued",
    cancelled: "Cancelled",
  })[state] ?? state.replaceAll("_", " ");

export function ResidentOverview({
  resident,
  snapshot,
  client,
  connected,
  onSelect,
  onNewTask,
}: {
  resident: Resident;
  snapshot: Snapshot;
  client: Client;
  connected: boolean;
  onSelect: (tab: ResidentTab) => void;
  onNewTask: () => void;
}) {
  const tasks = snapshot.tasks
    .filter((t) => t.resident_id === resident.id)
    .sort((a, b) => b.created_at - a.created_at);
  const runs = snapshot.runs.filter((r) => r.resident_id === resident.id);
  const unresolved = runs.filter((r) =>
    ["interrupted", "stopping"].includes(r.status),
  );
  const hold = resident.safety_hold_reason || resident.pause_reason;
  const blocked = !!hold || !!resident.unresolved_runs || unresolved.length > 0;
  return (
    <section className="resident-overview" aria-label="Resident information">
      <div className="resident-overview-heading">
        <div>
          <span className="eyebrow">At a glance</span>
          <h2>A clear view of {resident.name}’s work.</h2>
          <p>Recent work, current availability, and the essentials.</p>
        </div>
      </div>
      {blocked && (
        <div className="resident-attention">
          <div>
            <strong>
              {resident.unresolved_runs || unresolved.length
                ? "A previous run needs attention"
                : "New work is on hold"}
            </strong>
            <p>
              {hold
                ? hold.replaceAll("_", " ")
                : "The previous attempt must be resolved before new work can start. Requesting cancellation does not confirm termination."}
            </p>
          </div>
          <button onClick={() => onSelect("Activity")}>
            Review activity →
          </button>
        </div>
      )}
      <div className="resident-metrics">
        <div>
          <span>Availability</span>
          <strong className={`state state-${resident.presence}`}>
            {connected
              ? presenceLabel(resident.presence)
              : `Last known: ${presenceLabel(resident.presence)}`}
          </strong>
          <small>
            {connected
              ? "Reported by Hearth"
              : "Disconnected · waiting for fresh state"}
          </small>
        </div>
        <div>
          <span>Recent tasks</span>
          <strong>
            {tasks.length} <em>in recent history</em>
          </strong>
          <small>
            {tasks.filter((t) => t.status === "succeeded").length} completed ·{" "}
            {tasks.filter((t) => t.status === "queued").length} queued
          </small>
        </div>
        <div>
          <span>Daily spending limit</span>
          <strong>${(resident.daily_limit / 1e6).toFixed(2)}</strong>
          <small>
            API-equivalent estimate · {resident.budget_timezone ?? "UTC"}
          </small>
        </div>
      </div>
      <div className="resident-overview-grid">
        <div className="resident-main-column">
          <section className="resident-card">
            <div className="resident-card-head">
              <h2>Recent tasks</h2>
              <button className="quiet" onClick={() => onSelect("Tasks")}>
                All tasks →
              </button>
            </div>
            {!tasks.length ? (
              <p className="resident-card-empty">
                No tasks yet. Give {resident.name} something to work on.
              </p>
            ) : (
              <ul className="resident-recent-tasks">
                {tasks.slice(0, 3).map((task) => (
                  <li key={task.id}>
                    <button
                      onClick={() => {
                        onSelect("Tasks");
                        setTimeout(
                          () =>
                            document
                              .getElementById(`task-${task.id}`)
                              ?.scrollIntoView?.({ block: "center" }),
                          0,
                        );
                      }}
                    >
                      <span>
                        <strong>
                          {task.instruction || "Task instructions unavailable"}
                        </strong>
                        <time
                          dateTime={new Date(
                            task.created_at * 1000,
                          ).toISOString()}
                        >
                          {new Date(task.created_at * 1000).toLocaleString()}
                        </time>
                      </span>
                      <span className={`state state-${task.status}`}>
                        {presenceLabel(task.status)}
                      </span>
                      <span aria-hidden="true">↗</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="resident-card resident-purpose">
            <div className="resident-card-head">
              <h2>What {resident.name} does</h2>
            </div>
            <p>{resident.purpose}</p>
            <div className="resident-card-foot">
              Tools and actions stay within the permissions you grant.{" "}
              <button className="quiet" onClick={() => onSelect("Access")}>
                View access →
              </button>
            </div>
          </section>
          <div className="resident-card">
            <div className="resident-card-head">
              <h2>Recent activity</h2>
              <button className="quiet" onClick={() => onSelect("Activity")}>
                Full activity log →
              </button>
            </div>
            <ResidentActivityLog
              client={client}
              residentId={resident.id}
              residentName={resident.name}
              cursor={snapshot.cursor}
              connected={connected}
              compact
            />
          </div>
        </div>
        <aside className="resident-side-column">
          <section className="resident-card resident-quick-task">
            <span className="eyebrow">Your next idea</span>
            <h2>Something for {resident.name}?</h2>
            <p>Give them a task and follow its progress here.</p>
            <button className="primary" onClick={onNewTask}>
              ＋ New task
            </button>
          </section>
          <section className="resident-card">
            <div className="resident-card-head">
              <h2>Resident details</h2>
            </div>
            <dl className="resident-detail-list">
              <dt>Runtime</dt>
              <dd>
                {runtimeLabel(
                  snapshot.runtimes,
                  resident.profile?.execution_profile,
                )}
              </dd>
              <dt>Provider login</dt>
              <dd>
                {resident.logins?.[
                  resident.profile?.execution_profile ??
                    snapshot.runtimes.default
                ] === "resident"
                  ? "Resident’s own login"
                  : "Household login"}
              </dd>
              <dt>Budget timezone</dt>
              <dd>{resident.budget_timezone ?? "UTC"}</dd>
              <dt>Memory</dt>
              <dd>Revision {resident.memory_revision ?? 0}</dd>
            </dl>
            <button
              className="quiet resident-card-link"
              onClick={() => onSelect("Settings")}
            >
              All settings →
            </button>
          </section>
          <section className="resident-card resident-purpose">
            <div className="resident-card-head">
              <h2>Memory & context</h2>
            </div>
            <p>
              Read the current memory and the journal of this resident’s work.
            </p>
            <button
              className="quiet resident-card-link"
              onClick={() => onSelect("Memory")}
            >
              Read memory →
            </button>
          </section>
        </aside>
      </div>
    </section>
  );
}
