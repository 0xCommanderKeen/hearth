import { useState } from "react";
import {
  Client,
  type MemoryRevision,
  type Resident,
} from "../../shared/client";
import { diffLines, type DiffLine } from "../../shared/diff";
import { RunLink } from "./RunLink";

const PAGE = 20;
const stamp = (at: number) => new Date(at * 1000).toLocaleString();

type Loaded = { revisions: MemoryRevision[]; total: number };

export function authorLabel(revision: MemoryRevision) {
  return revision.author === "run"
    ? "Written by a run"
    : "Written by the operator";
}

export function MemoryHistory({
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
  const [compared, setCompared] = useState<{
    revision: number;
    lines: DiffLine[];
  } | null>(null);
  const read = () =>
    act(async () => {
      const page = await client.memoryHistory(resident.id, PAGE, 0);
      setLoaded({ revisions: page.revisions, total: page.total });
      setCompared(null);
    });
  // Revisions are never deleted, so a well-used note passes any single page. Paging
  // moves the window by offset rather than growing the page past the route's bound.
  const older = (from: Loaded) =>
    act(async () => {
      const page = await client.memoryHistory(
        resident.id,
        PAGE,
        from.revisions.length,
      );
      const seen = new Set(from.revisions.map((item) => item.revision));
      setLoaded({
        revisions: [
          ...from.revisions,
          ...page.revisions.filter((item) => !seen.has(item.revision)),
        ],
        total: page.total,
      });
    });
  const compare = (revision: number) =>
    act(async () => {
      const after = await client.memoryRevision(resident.id, revision);
      const before = await client.memoryRevision(resident.id, revision - 1);
      setCompared({ revision, lines: diffLines(before.text, after.text) });
    });
  return (
    <details className="skill-editor">
      <summary>{resident.name} · Memory history</summary>
      <p>
        Every revision of this note, newest first. Hearth records the author
        from the writer it authenticated, never from the text: an operator save
        reads operator, and a resident's own run reads run.
      </p>
      <button disabled={busy} onClick={() => void read()}>
        {loaded ? "Reload memory history" : "Read memory history"}
      </button>
      {loaded && loaded.total === 0 && <p>No memory has been saved yet.</p>}
      {loaded && loaded.total > 0 && (
        <ol
          className="memory-history"
          aria-label={`Memory history for ${resident.name}`}
        >
          {loaded.revisions.map((item, index) => (
            <li key={item.revision}>
              <div className="memory-revision">
                <strong>
                  Revision {item.revision}
                  {index === 0 ? " · current" : ""}
                </strong>
                <span className={`chip ${item.author}`}>
                  {authorLabel(item)}
                </span>
                <time>{stamp(item.created_at)}</time>
              </div>
              <div className="task-actions">
                {item.run_id && (
                  <RunLink
                    runId={item.run_id}
                    openable={openable(item.run_id)}
                    label="Open the run →"
                  />
                )}
                <button
                  disabled={busy}
                  onClick={() => void compare(item.revision)}
                >
                  Show what changed
                </button>
                <small>
                  {item.size} bytes · {item.sha256.slice(0, 12)}
                </small>
              </div>
              {compared?.revision === item.revision &&
                (compared.lines.length === 0 ? (
                  <p role="status">
                    Revision {item.revision} repeats the previous text exactly.
                  </p>
                ) : (
                  <pre
                    className="memory-diff"
                    aria-label={`Changes in memory revision ${item.revision}`}
                  >
                    {compared.lines.map((line, position) => (
                      <span className={`diff-${line.kind}`} key={position}>
                        {line.kind === "added"
                          ? "+"
                          : line.kind === "removed"
                            ? "-"
                            : " "}
                        {line.text}
                        {"\n"}
                      </span>
                    ))}
                  </pre>
                ))}
            </li>
          ))}
        </ol>
      )}
      {loaded && loaded.total > loaded.revisions.length && (
        <button disabled={busy} onClick={() => void older(loaded)}>
          Show older revisions ({loaded.total - loaded.revisions.length} more)
        </button>
      )}
    </details>
  );
}
