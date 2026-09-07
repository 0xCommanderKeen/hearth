import { useState } from "react";
import {
  Client,
  type MemoryHistory as History,
  type MemoryRevision,
  type Resident,
} from "../../shared/client";
import { diffLines, type DiffLine } from "../../shared/diff";

const PAGE = 20;
// The route bounds a page at 100 revisions; asking for more is refused, not truncated.
const MAX_PAGE = 100;
const stamp = (at: number) => new Date(at * 1000).toLocaleString();

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
}: {
  client: Client;
  resident: Resident;
  busy: boolean;
  act: (operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [history, setHistory] = useState<History | null>(null);
  const [compared, setCompared] = useState<{
    revision: number;
    lines: DiffLine[];
  } | null>(null);
  const load = (limit: number) =>
    act(async () => {
      setHistory(await client.memoryHistory(resident.id, limit));
      setCompared(null);
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
      <button disabled={busy} onClick={() => void load(PAGE)}>
        {history ? "Reload memory history" : "Read memory history"}
      </button>
      {history && history.total === 0 && <p>No memory has been saved yet.</p>}
      {history && history.total > 0 && (
        <ol
          className="memory-history"
          aria-label={`Memory history for ${resident.name}`}
        >
          {history.revisions.map((item, index) => (
            <li key={item.revision}>
              <div className="memory-revision">
                <strong>
                  Revision {item.revision}
                  {index === 0 && history.offset === 0 ? " · current" : ""}
                </strong>
                <span className={`chip ${item.author}`}>
                  {authorLabel(item)}
                </span>
                <time>{stamp(item.created_at)}</time>
              </div>
              <div className="task-actions">
                {item.run_id && (
                  <a href={`#run-${encodeURIComponent(item.run_id)}`}>
                    Open the run →
                  </a>
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
      {history &&
        history.total > history.revisions.length &&
        history.revisions.length < MAX_PAGE && (
          <button
            disabled={busy}
            onClick={() =>
              void load(Math.min(history.revisions.length + PAGE, MAX_PAGE))
            }
          >
            Show older revisions ({history.total - history.revisions.length}{" "}
            more)
          </button>
        )}
    </details>
  );
}
