import { useRef, useState } from "react";
import { Client, RequestError, type Snapshot } from "../../shared/client";

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
  const pending = useRef<{ id: string; payload: string } | null>(null);
  const submitting = useRef(false);
  const routines = snapshot.routines ?? [];
  const [residentId, setResidentId] = useState("");
  const [instruction, setInstruction] = useState("");
  const [localTime, setLocalTime] = useState("09:00");
  const [timezone, setTimezone] = useState(
    Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  const eligible = snapshot.residents.filter(
    (r) => r.lifecycle?.state !== "archived",
  );
  const selected = residentId || eligible[0]?.id || "";
  const readOnly = Boolean(snapshot.restore_hold);
  return (
    <section className="output" aria-label="Daily routines">
      <span className="eyebrow">TOWNHALL / DAILY ROUTINES</span>
      <h2>A little work, every day.</h2>
      <p>
        Run an assignment daily while Hearth is running. After an outage, only
        the newest due occurrence is queued. An unfinished run prevents another
        from piling up.
      </p>
      {!routines.length && (
        <p className="muted">
          No routines yet. Schedule one for a resident below.
        </p>
      )}
      <ul className="tasks">
        {routines.map((routine) => {
          const resident = snapshot.residents.find(
            (r) => r.id === routine.resident_id,
          );
          const lifecycle = resident?.lifecycle?.state;
          const suspended = lifecycle === "paused" || lifecycle === "archived";
          const locked = readOnly || lifecycle === "archived";
          const occurrences = (snapshot.occurrences ?? [])
            .filter((o) => o.routine_id === routine.id)
            .slice(0, 5);
          return (
            <li key={routine.id} id={`routine-${routine.id}`}>
              <h3>{resident?.name ?? routine.resident_id}</h3>
              <p>
                Daily at {routine.local_time} ({routine.timezone}) ·{" "}
                {routine.enabled ? "Enabled" : "Disabled"} · revision{" "}
                {routine.revision}
              </p>
              <p className="muted">{routine.instruction}</p>
              {suspended && (
                <p>
                  Suspended by {lifecycle} lifecycle. Existing tasks retain
                  their status.
                </p>
              )}
              {Boolean(routine.enabled) && !suspended && (
                <p>Next: {new Date(routine.next_at * 1000).toLocaleString()}</p>
              )}
              <div className="task-actions">
                <button
                  disabled={busy || locked}
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
                  {routine.enabled
                    ? "Disable daily routine"
                    : "Enable daily routine"}
                </button>
              </div>
              {occurrences.length > 0 && (
                <ul className="tasks">
                  {occurrences.map((o) => (
                    <li key={o.scheduled_at}>
                      {new Date(o.scheduled_at * 1000).toLocaleString()} ·{" "}
                      {o.status === "skipped_overlap"
                        ? "Skipped: previous task unfinished"
                        : "Task created"}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      <p className="muted">
        Disabling stops future occurrences. Tasks already queued keep their
        existing lifecycle. A repeated clock time runs once at its earlier
        occurrence. A clock time skipped by daylight saving does not run that
        day.
      </p>
      {eligible.length > 0 && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (submitting.current || busy || readOnly) return;
            const body = {
              resident_id: selected,
              instruction,
              local_time: localTime,
              timezone,
              enabled: true,
              expected_revision: 0,
            };
            const payload = JSON.stringify(body);
            if (pending.current?.payload !== payload)
              pending.current = { id: crypto.randomUUID(), payload };
            const attempt = pending.current;
            submitting.current = true;
            void act(async () => {
              try {
                try {
                  await client.saveRoutine(attempt.id, body);
                } catch (error) {
                  if (
                    !(error instanceof RequestError) ||
                    error.message !== "revision conflict"
                  )
                    throw error;
                  // Creation may have committed before its response was lost. Never
                  // replay it as an edit or replace a concurrently changed schedule.
                  const current = (await client.state()).routines?.find(
                    (r) => r.id === attempt.id,
                  );
                  if (
                    !current ||
                    current.resident_id !== body.resident_id ||
                    current.instruction !== body.instruction ||
                    current.local_time !== body.local_time ||
                    current.timezone !== body.timezone ||
                    Boolean(current.enabled) !== body.enabled
                  )
                    throw error;
                }
                pending.current = null;
                setInstruction((value) =>
                  value === body.instruction ? "" : value,
                );
              } finally {
                submitting.current = false;
              }
            });
          }}
        >
          <h3>Schedule a daily routine</h3>
          <label htmlFor="routine-resident">Resident</label>
          <select
            id="routine-resident"
            value={selected}
            onChange={(e) => setResidentId(e.target.value)}
          >
            {eligible.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
          <label htmlFor="routine-instruction">The daily assignment</label>
          <textarea
            id="routine-instruction"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            required
            maxLength={32000}
          />
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
          <button disabled={busy || readOnly || !selected}>
            Schedule daily routine
          </button>
        </form>
      )}
    </section>
  );
}
