import { useState } from "react";
import { Client, type Resident, type ResidentMemory } from "./client";

export function Memory({
  client,
  resident,
  busy,
  readOnly,
  act,
}: {
  client: Client;
  resident: Resident;
  busy: boolean;
  readOnly: boolean;
  act: (operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [saved, setSaved] = useState<ResidentMemory | null>(null);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState("");
  const changed =
    saved !== null && saved.revision !== (resident.memory_revision ?? 0);
  return (
    <details className="skill-editor">
      <summary>{resident.name} · Memory</summary>
      <p>
        Notes carried from one task to the next. You can edit this Markdown;
        Reader can only read the version pinned to its run.
      </p>
      <button
        disabled={busy}
        onClick={() =>
          void act(async () => {
            const current = await client.memory(resident.id);
            setSaved(current);
            setDraft(current.text);
            setNotice("");
          })
        }
      >
        {saved ? "Load current memory (replace draft)" : "Read memory"}
      </button>
      {saved && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (busy || readOnly || changed) return;
            void act(async () => {
              const result = await client.saveMemory(
                resident.id,
                draft,
                saved.revision,
              );
              setSaved(result);
              setNotice(`Saved memory revision ${result.revision}.`);
            });
          }}
        >
          <label htmlFor={`memory-${resident.id}`}>
            Memory for {resident.name}
          </label>
          <textarea
            id={`memory-${resident.id}`}
            value={draft}
            maxLength={131072}
            disabled={busy || readOnly}
            onChange={(event) => {
              setDraft(event.target.value);
              setNotice("");
            }}
          />
          <small>
            Memory revision {saved.revision}. Up to 128 KiB of UTF-8 text.
            Existing runs keep their original memory; new runs use the latest
            saved revision.
          </small>
          {changed && (
            <p role="status">
              Memory changed while this draft was open. Your draft is retained.
              Load the current memory before saving again.
            </p>
          )}
          {readOnly && <p>This restored copy is read-only.</p>}
          <button
            disabled={busy || readOnly || changed || draft === saved.text}
          >
            Save memory
          </button>
          {notice && <p role="status">{notice}</p>}
        </form>
      )}
    </details>
  );
}
