import { useState } from "react";
import {
  Client,
  type ResidentJournal,
  type Resident,
} from "../../shared/client";

const PAGE = 20;
// The route bounds a page at 100 entries; asking for more is refused, not truncated.
const MAX_PAGE = 100;
const stamp = (at: number) => new Date(at * 1000).toLocaleString();

export function Journal({
  client,
  resident,
  busy,
  act,
}: {
  client: Client;
  resident: Resident;
  busy: boolean;
  act: (operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [journal, setJournal] = useState<ResidentJournal | null>(null);
  const load = (limit: number) =>
    act(async () => setJournal(await client.journal(resident.id, limit)));
  return (
    <details className="skill-editor">
      <summary>{resident.name} · Journal</summary>
      <p>
        What this resident's own runs wrote, newest first. Hearth never writes
        an entry on a resident's behalf, never edits one and never summarizes
        work into one, so an empty journal means its runs wrote none.
      </p>
      <button disabled={busy} onClick={() => void load(PAGE)}>
        {journal ? "Reload journal" : "Read journal"}
      </button>
      {journal && journal.total === 0 && (
        <p>No run has written an entry yet.</p>
      )}
      {journal && journal.total > 0 && (
        <ol className="journal" aria-label={`Journal for ${resident.name}`}>
          {journal.entries.map((entry) => (
            <li key={entry.sequence}>
              <div className="memory-revision">
                <strong>Entry {entry.sequence}</strong>
                <time>{stamp(entry.at)}</time>
              </div>
              <p className="journal-text">{entry.text}</p>
              <a href={`#run-${encodeURIComponent(entry.run_id)}`}>
                Open the run that wrote it →
              </a>
            </li>
          ))}
        </ol>
      )}
      {journal &&
        journal.total > journal.entries.length &&
        journal.entries.length < MAX_PAGE && (
          <button
            disabled={busy}
            onClick={() =>
              void load(Math.min(journal.entries.length + PAGE, MAX_PAGE))
            }
          >
            Show older entries ({journal.total - journal.entries.length} more)
          </button>
        )}
      {journal && journal.total > 0 && (
        <small>
          Older entries roll out of this list into immutable files as the
          household journal bound is reached. Nothing is deleted.
        </small>
      )}
    </details>
  );
}
