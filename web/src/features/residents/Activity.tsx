import { useEffect, useState } from "react";
import { Client, type ResidentActivity } from "../../shared/client";

const labels: Record<string, string> = {
  "journal.written": "Journal entry written",
  "management.tool_completed": "Management action completed",
  "task.queued": "Task queued",
  "run.admitted": "Run admitted",
  "run.launch_requested": "Starting agent",
  "run.running": "Agent reported running",
  "run.interrupted": "Outcome unknown",
  "run.stopping": "Stopping agent",
  "run.succeeded": "Run succeeded",
  "run.failed": "Run failed",
  "run.cancelled": "Run cancelled",
  "run.cancel_requested": "Cancellation requested",
  "sandbox.absence_verified": "Agent container absence confirmed",
  "run.waiting": "Run waiting",
};

export function ResidentActivityLog({
  client,
  residentId,
  residentName,
  cursor,
  connected,
  compact = false,
}: {
  client: Client;
  residentId: string;
  residentName: string;
  cursor: number;
  connected: boolean;
  compact?: boolean;
}) {
  const [before, setBefore] = useState<number | null>(null);
  const [page, setPage] = useState<ResidentActivity | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let current = true;
    if (!connected) return;
    setLoading(true);
    void client.activity(residentId, before).then(
      (value) => {
        if (!current) return;
        setPage(value);
        setError(false);
        setLoading(false);
      },
      () => {
        if (!current) return;
        setError(true);
        setLoading(false);
      },
    );
    return () => {
      current = false;
    };
  }, [client, residentId, cursor, before, connected, reload]);

  return (
    <section
      className="resident-activity"
      aria-label={`Activity for ${residentName}`}
    >
      {!compact && (
        <header className="resident-tab-heading">
          <span className="eyebrow">{residentName} / Activity</span>
          <h2>Activity log</h2>
        </header>
      )}
      {!compact && (
        <p>
          Recorded events for {residentName}, newest first. Updates as work
          progresses.
        </p>
      )}
      {!connected && (
        <p role="status">Disconnected. Showing the last loaded activity.</p>
      )}
      {error && (
        <p role="alert">
          Could not load activity. Any displayed events are from the last
          successful load.
        </p>
      )}
      {loading && !page && <p role="status">Loading activity…</p>}
      {!compact && (
        <button
          disabled={loading || !connected}
          onClick={() => setReload((value) => value + 1)}
        >
          Refresh activity
        </button>
      )}
      {!compact && before !== null && (
        <button
          disabled={loading || !connected}
          onClick={() => {
            setPage(null);
            setBefore(null);
          }}
        >
          Latest activity
        </button>
      )}
      {page?.entries.length === 0 && <p>No recorded activity yet.</p>}
      <ol className="journal">
        {(compact ? page?.entries.slice(0, 4) : page?.entries)?.map(
          (entry, index) => (
            <li key={entry.sequence}>
              {!compact &&
                (index === 0 ||
                  new Date(
                    page!.entries[index - 1].at * 1000,
                  ).toDateString() !==
                    new Date(entry.at * 1000).toDateString()) && (
                  <h3 className="activity-day">
                    {new Date(entry.at * 1000).toLocaleDateString(undefined, {
                      weekday: "long",
                      month: "long",
                      day: "numeric",
                    })}
                  </h3>
                )}
              <div className="memory-revision">
                <strong>
                  {labels[entry.kind] ??
                    entry.kind.replaceAll(".", " · ").replaceAll("_", " ")}
                </strong>
                <time dateTime={new Date(entry.at * 1000).toISOString()}>
                  {new Date(entry.at * 1000).toLocaleString()}
                </time>
              </div>
              {entry.diagnostic ? (
                <>
                  <p>{entry.diagnostic.message}</p>
                  {!entry.diagnostic.turn_started && (
                    <p>No model turn was recorded as started.</p>
                  )}
                  <small>Reason: {entry.diagnostic.code}</small>
                </>
              ) : entry.kind === "run.interrupted" ? (
                <p>
                  No confirmed outcome is available. This does not mean the run
                  succeeded or is safe to retry.
                </p>
              ) : null}
              {entry.run_id && (
                <p>
                  <a href={`#runs/${encodeURIComponent(entry.run_id)}`}>
                    Open run →
                  </a>
                </p>
              )}
            </li>
          ),
        )}
      </ol>
      {!compact && page?.next_before != null && (
        <button
          disabled={loading || !connected}
          onClick={() => {
            setBefore(page.next_before);
            setPage(null);
          }}
        >
          Older activity
        </button>
      )}
    </section>
  );
}
