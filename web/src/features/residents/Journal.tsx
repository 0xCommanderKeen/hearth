import { useState } from "react";
import { Client, type JournalEntry, type Resident } from "../../shared/client";
import { RunLink } from "./RunLink";

const PAGE = 20;
const stamp = (at: number) => new Date(at * 1000).toLocaleString();

type Loaded = { entries: JournalEntry[]; total: number };

export function Journal({
  client,
  resident,
  busy,
  act,
  openable,
}: {
  client: Client;
  resident: Resident;
  busy: boolean;
  act: (operation: () => Promise<unknown>) => Promise<void>;
  openable: (runId: string) => boolean;
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const read = () =>
    act(async () => {
      const page = await client.journal(resident.id, PAGE, 0);
      setLoaded({ entries: page.entries, total: page.total });
    });
  // Paging moves the window rather than growing it, so a journal longer than one page
  // stays reachable. Retention may roll an entry out between pages; keeping the
  // sequences already shown avoids repeating one that shifted under the offset.
  const older = (from: Loaded) =>
    act(async () => {
      const page = await client.journal(resident.id, PAGE, from.entries.length);
      const seen = new Set(from.entries.map((entry) => entry.sequence));
      setLoaded({
        entries: [
          ...from.entries,
          ...page.entries.filter((entry) => !seen.has(entry.sequence)),
        ],
        total: page.total,
      });
    });
  return (
    <details className="skill-editor">
      <summary>{resident.name} · Journal</summary>
      <p>
        What this resident's own runs wrote, newest first. Hearth never writes
        an entry on a resident's behalf, never edits one and never summarizes
        work into one, so an empty journal means its runs wrote none.
      </p>
      <button disabled={busy} onClick={() => void read()}>
        {loaded ? "Reload journal" : "Read journal"}
      </button>
      {loaded && loaded.total === 0 && <p>No run has written an entry yet.</p>}
      {loaded && loaded.total > 0 && (
        <ol className="journal" aria-label={`Journal for ${resident.name}`}>
          {loaded.entries.map((entry) => (
            <li key={entry.sequence}>
              <div className="memory-revision">
                <strong>Entry {entry.sequence}</strong>
                <time>{stamp(entry.at)}</time>
              </div>
              <p className="journal-text">{entry.text}</p>
              <RunLink
                runId={entry.run_id}
                openable={openable(entry.run_id)}
                label="Open the run that wrote it →"
              />
            </li>
          ))}
        </ol>
      )}
      {loaded && loaded.total > loaded.entries.length && (
        <button disabled={busy} onClick={() => void older(loaded)}>
          Show older entries ({loaded.total - loaded.entries.length} more)
        </button>
      )}
      {loaded && loaded.total > 0 && (
        <small>
          Older entries roll out of this list into immutable files as the
          household journal bound is reached. Nothing is deleted.
        </small>
      )}
    </details>
  );
}
