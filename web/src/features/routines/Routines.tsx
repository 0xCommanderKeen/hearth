import { useState } from "react";
import { Client, type Snapshot } from "../../shared/client";

export function RoutinePanel({
  client,
  snapshot,
  busy,
  act,
}: {
  client: Client;
  snapshot: Snapshot;
  busy: boolean;
  act: (operation: () => Promise<unknown>) => Promise<void>;
}) {
  const routine = snapshot.routines?.find((r) => r.id === "reader-daily");
  const lifecycle = snapshot.residents.find(
    (r) => r.id === (routine?.resident_id ?? "reader"),
  )?.lifecycle?.state;
  const suspended = lifecycle === "paused" || lifecycle === "archived";
  const readOnly = snapshot.restore_hold || lifecycle === "archived";
  const [localTime, setLocalTime] = useState("09:00");
  const [timezone, setTimezone] = useState("Europe/Ljubljana");
  return (
    <section className="output" aria-label="Daily mock routine">
      <span className="eyebrow">TOWNHALL / DAILY ROUTINE</span>
      <h2>A little work, every day.</h2>
      <p>
        Run a synthetic summary daily while Hearth is running. After an outage,
        only the newest due summary is queued. An unfinished summary prevents
        another from piling up.
      </p>
      {routine ? (
        <>
          <p>
            Daily at {routine.local_time} ({routine.timezone}) ·{" "}
            {routine.enabled ? "Enabled" : "Disabled"} · revision{" "}
            {routine.revision}
          </p>
          {suspended && (
            <p>
              Suspended by {lifecycle} lifecycle. Existing tasks retain their
              status.
            </p>
          )}
          {Boolean(routine.enabled) && !suspended && (
            <p>Next: {new Date(routine.next_at * 1000).toLocaleString()}</p>
          )}
          <button
            disabled={busy || readOnly}
            onClick={() =>
              void act(() =>
                client.saveRoutine(routine.id, {
                  resident_id: routine.resident_id,
                  instruction: routine.instruction,
                  local_time: routine.local_time,
                  timezone: routine.timezone,
                  enabled: !routine.enabled,
                  expected_revision: routine.revision,
                }),
              )
            }
          >
            {routine.enabled ? "Disable daily routine" : "Enable daily routine"}
          </button>
          <p className="muted">
            Disabling stops future occurrences. Tasks already queued keep their
            existing lifecycle.
          </p>
        </>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void act(() =>
              client.saveRoutine("reader-daily", {
                resident_id: "reader",
                instruction: "Summarize today’s synthetic notes.",
                local_time: localTime,
                timezone,
                enabled: true,
                expected_revision: 0,
              }),
            );
          }}
        >
          <label htmlFor="routine-time">Daily time</label>
          <input
            id="routine-time"
            type="time"
            value={localTime}
            onChange={(e) => setLocalTime(e.target.value)}
            required
          />
          <label htmlFor="routine-zone">Timezone</label>
          <input
            id="routine-zone"
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            required
            maxLength={100}
          />
          <button disabled={busy || readOnly || !snapshot.residents.length}>
            Enable daily mock summary
          </button>
        </form>
      )}
      <p className="muted">
        A repeated clock time runs once at its earlier occurrence. A clock time
        skipped by daylight saving does not run that day.
      </p>
      <ul className="tasks">
        {snapshot.occurrences
          ?.filter((o) => o.routine_id === "reader-daily")
          .slice(0, 5)
          .map((o) => (
            <li key={o.scheduled_at}>
              {new Date(o.scheduled_at * 1000).toLocaleString()} ·{" "}
              {o.status === "skipped_overlap"
                ? "Skipped: previous task unfinished"
                : "Task created"}
            </li>
          ))}
      </ul>
    </section>
  );
}
