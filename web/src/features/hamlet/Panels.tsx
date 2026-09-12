import { useEffect, useRef } from "react";
import { placeByIdentity, residentLocation } from "./village/places";
import type { Snapshot } from "../../shared/client";

const state = (value: string) =>
  value === "interrupted" ? "Outcome unknown" : value;
const money = (value: number) => `$${(value / 1e6).toFixed(2)}`;

/** A projection of available records. Opening a record uses the existing record route. */
export function ContextPanel({
  identity,
  snapshot,
  connected,
  active,
  onClose,
  onEnter,
}: {
  identity: string;
  snapshot: Snapshot;
  connected: boolean;
  active: boolean;
  onClose(): void;
  onEnter?(): void;
}) {
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (active) {
      panel.current?.focus({ preventScroll: true });
      if (window.matchMedia?.("(max-width: 640px)").matches)
        panel.current?.parentElement
          ?.querySelector(".scene-canvas")
          ?.scrollIntoView?.({ block: "start" });
    }
  }, [active]);
  const resident = snapshot.residents.find(
    (r) => identity === `#residents/${encodeURIComponent(r.id)}`,
  );
  const townhall = identity === "#townhall";
  const place = placeByIdentity(identity);
  const occupants = snapshot.residents
    .map((r) => ({ resident: r, location: residentLocation(r, snapshot) }))
    .filter(
      ({ location }) =>
        location.run && location.destination === (townhall ? null : place?.id),
    );
  const runs = (snapshot.runs ?? []).filter(
    (r) => r.resident_id === resident?.id,
  );
  const tasks = (snapshot.tasks ?? []).filter(
    (t) => t.resident_id === resident?.id,
  );
  const current = runs.filter((r) =>
    ["starting", "running", "stopping", "interrupted"].includes(r.status),
  );
  const latest = [...runs]
    .filter((r) => r.artifact_id && typeof r.finished_at === "number")
    .sort(
      (a, b) => b.finished_at! - a.finished_at! || b.id.localeCompare(a.id),
    )[0];
  const household = snapshot.household;
  return (
    <aside
      ref={panel}
      tabIndex={-1}
      role="dialog"
      aria-label={
        townhall
          ? "Townhall household"
          : place
            ? place.name
            : `${resident?.name ?? "Unavailable resident"} at home`
      }
      className="village-panel"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="section-title">
        <span className="eyebrow">
          {townhall ? "THE HOUSEHOLD" : place ? "VILLAGE WORK" : "AT HOME"}
        </span>
        <button onClick={onClose} aria-label="Close village panel">
          Close ×
        </button>
      </div>
      <h2>
        {townhall
          ? "Townhall"
          : (place?.name ?? resident?.name ?? "Resident unavailable")}
      </h2>
      {onEnter &&
        (townhall ||
          (resident && resident.lifecycle?.state !== "archived")) && (
          <button className="room-enter" onClick={onEnter}>
            {townhall ? "Enter Townhall" : "Enter home"} →
          </button>
        )}
      {!connected && (
        <p className="notice">
          Disconnected · showing last known records. Reconnecting does not
          establish a run's outcome.
        </p>
      )}
      {snapshot.restore_hold && (
        <p className="notice">Restore hold · household mutations are held.</p>
      )}
      {(place || townhall) && (
        <div className="place-work">
          {place && <p>{place.description}</p>}
          <h3>{connected ? "Recorded work here" : "Last known work here"}</h3>
          {occupants.length ? (
            <ul>
              {occupants.map(({ resident: r, location }) => (
                <li key={r.id}>
                  <a href={`#runs/${encodeURIComponent(location.run!.id)}`}>
                    {r.name} · {location.run!.action?.label ?? "Running work"}
                  </a>
                  {location.run!.action && (
                    <small>
                      {" "}
                      ·{" "}
                      {new Date(
                        location.run!.action.at * 1000,
                      ).toLocaleTimeString()}
                    </small>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p>No running work located here in the available records.</p>
          )}
          {place && <a href={place.href}>{place.link} →</a>}
          {place?.key === "post" && (
            <p>
              {(snapshot.letters ?? []).length} retained letter events. See
              Recent post below the village for recorded senders, recipients and
              times.
            </p>
          )}
          <small>Based on the current run and its last recorded action.</small>
        </div>
      )}
      {place ? null : townhall ? (
        <>
          <p>The household’s work, allowances and matters needing attention.</p>
          <dl>
            <dt>Residents</dt>
            <dd>
              {
                snapshot.residents.filter(
                  (r) => r.lifecycle?.state !== "archived",
                ).length
              }{" "}
              in the village ·{" "}
              {
                snapshot.residents.filter(
                  (r) => r.lifecycle?.state === "archived",
                ).length
              }{" "}
              archived
            </dd>
            <dt>Work</dt>
            <dd>
              {household
                ? `${household.active_runs} active runs · concurrency limit ${household.concurrency_limit}`
                : "Household work totals unavailable"}
            </dd>
            <dt>Allowance</dt>
            <dd>
              {household
                ? `${money(household.remaining)} remaining of ${money(household.daily_limit)} · ${household.budget_day} (${household.timezone})`
                : "Household allowance unavailable"}
            </dd>
            {household && (
              <>
                <dt>Accounting</dt>
                <dd>
                  {money(household.spent)} spent · {money(household.reserved)}{" "}
                  reserved · {money(household.unknown)} held for unknown usage
                </dd>
              </>
            )}
          </dl>
          <p>
            {(snapshot.runs ?? []).filter((r) => r.status === "failed").length}{" "}
            failed run(s) in available records ·{" "}
            {
              (snapshot.runs ?? []).filter((r) => r.status === "interrupted")
                .length
            }{" "}
            outcome unknown ·{" "}
            {
              snapshot.residents.filter(
                (r) =>
                  r.operator_paused ||
                  r.presence === "paused" ||
                  r.pause_reason,
              ).length
            }{" "}
            paused resident(s).
          </p>
          <nav aria-label="Household records">
            <a href="#townhall">Open Townhall →</a>
            <a href="#tasks">Tasks &amp; results →</a>
            <a href="#inbox">Inbox →</a>
            <a href="#residents-archived">Archived residents &amp; holds →</a>
          </nav>
        </>
      ) : resident ? (
        <>
          <p className="village-purpose">
            {resident.purpose || "Purpose unavailable in this snapshot."}
          </p>
          <p className="state">
            {connected ? "Recorded status" : "Last known status"}:{" "}
            {state(resident.presence)}
          </p>
          <p>
            {connected ? "Village location" : "Last known location"}:{" "}
            {residentLocation(resident, snapshot).label}
          </p>
          {resident.lifecycle && <p>Lifecycle: {resident.lifecycle.state}</p>}
          {(resident.operator_paused || resident.pause_reason) && (
            <p>
              Paused
              {resident.pause_reason
                ? ` · ${resident.pause_reason}`
                : " by operator"}
            </p>
          )}
          {!!resident.unresolved_runs && (
            <p className="notice">
              {resident.lifecycle?.state === "archived" ? "Archived with " : ""}
              {resident.unresolved_runs} unresolved run(s). Accounting holds
              remain.
            </p>
          )}
          {resident.safety_hold_reason && (
            <p className="notice">Safety hold: {resident.safety_hold_reason}</p>
          )}
          <h3>Current work</h3>
          {current.length ? (
            <ul>
              {current.map((run) => (
                <li key={run.id}>
                  <a href={`#runs/${encodeURIComponent(run.id)}`}>
                    {state(run.status)} ·{" "}
                    {tasks.find((t) => t.id === run.task_id)?.instruction ??
                      `Task ${run.task_id} · instruction outside available records`}
                    {tasks.find((t) => t.id === run.task_id)
                      ?.instruction_truncated && "…"}
                  </a>
                  {!run.usage_known && " · usage unknown"}
                </li>
              ))}
            </ul>
          ) : (
            <p>
              No active run in the available records. This is not a complete
              work history.
            </p>
          )}
          {tasks
            .filter((t) => t.status === "queued")
            .map((t) => (
              <p key={t.id}>
                Queued · {t.instruction}
                {t.instruction_truncated && "…"}
              </p>
            ))}
          <h3>Latest available result</h3>
          {latest ? (
            <p>
              <a href={`#runs/${encodeURIComponent(latest.id)}`}>
                Read result →
              </a>
              <br />
              {state(latest.status)} ·{" "}
              {new Date(latest.finished_at! * 1000).toLocaleString()}
              {!latest.usage_known && " · usage unknown"}
            </p>
          ) : (
            <p>
              No dated result available in this snapshot. Older or unavailable
              results are not evidence of no previous work.
            </p>
          )}
          {!!runs.filter((r) => r.status === "failed").length && (
            <p>
              Failed runs in available records:{" "}
              {runs
                .filter((r) => r.status === "failed")
                .map((r) => (
                  <a key={r.id} href={`#runs/${encodeURIComponent(r.id)}`}>
                    {r.id} →{" "}
                  </a>
                ))}
            </p>
          )}
          <nav aria-label="Resident records">
            <a href={`${identity}?panel=journal`}>Journal →</a>
            <a href={`${identity}?panel=letters`}>Letters →</a>
            <a href={identity}>Full profile →</a>
          </nav>
        </>
      ) : (
        <p>
          This selected identity is no longer available in the household
          snapshot. It has not been replaced by another resident.
        </p>
      )}
      <small className="village-bound">
        Snapshot window: up to{" "}
        {snapshot.limits?.tasks ?? "an unspecified number of"} tasks and{" "}
        {snapshot.limits?.runs ?? "an unspecified number of"} runs across the
        household; active and unresolved work is prioritized. Counts here are
        for available records unless labeled as household totals. Journal,
        letters and result contents load only when requested.
      </small>
    </aside>
  );
}
