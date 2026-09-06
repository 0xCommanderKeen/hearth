import { useState } from "react";
import { type Resident } from "./client";

export type EditorControls = {
  resident: Resident;
  busy: boolean;
  readOnly: boolean;
  act: (operation: () => Promise<unknown>) => Promise<void>;
};

export function TextEditor<T extends { revision: number }>({
  resident,
  busy,
  readOnly,
  act,
  kind,
  revision,
  load,
  save,
  text,
  limit,
  description,
  hint,
}: EditorControls & {
  kind: "memory" | "resident instructions";
  revision: number;
  load: () => Promise<T>;
  save: (saved: T, draft: string) => Promise<T>;
  text: (saved: T) => string;
  limit: number;
  description: string;
  hint: (revision: number) => string;
}) {
  const [saved, setSaved] = useState<T | null>(null);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState("");
  const changed = saved !== null && saved.revision !== revision;
  const memory = kind === "memory";
  const label = memory ? "Memory" : "Resident instructions";
  const id = `${memory ? "memory" : "skills"}-${resident.id}`;
  return (
    <details className="skill-editor">
      <summary>
        {resident.name} · {label}
      </summary>
      <p>{description}</p>
      <button
        disabled={busy}
        onClick={() =>
          void act(async () => {
            const current = await load();
            setSaved(current);
            setDraft(text(current));
            setNotice("");
          })
        }
      >
        {saved
          ? `Load current ${memory ? "memory" : "revision"} (replace draft)`
          : `Read ${kind}`}
      </button>
      {saved && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (busy || readOnly || changed) return;
            void act(async () => {
              const result = await save(saved, draft);
              setSaved(result);
              setNotice(
                memory
                  ? `Saved memory revision ${result.revision}.`
                  : `Saved resident instructions at revision ${result.revision}.`,
              );
            });
          }}
        >
          <label htmlFor={id}>
            {label} for {resident.name}
          </label>
          <textarea
            id={id}
            value={draft}
            maxLength={limit}
            disabled={busy || readOnly}
            onChange={(event) => {
              setDraft(event.target.value);
              setNotice("");
            }}
          />
          <small>{hint(saved.revision)}</small>
          {changed && (
            <p role="status">
              {memory ? "Memory" : "The resident"} changed while this draft was
              open. Your draft is retained. Load the current{" "}
              {memory ? "memory" : "revision"} before saving again.
            </p>
          )}
          {readOnly && <p>This restored copy is read-only.</p>}
          <button
            disabled={busy || readOnly || changed || draft === text(saved)}
          >
            Save {kind}
          </button>
          {notice && <p role="status">{notice}</p>}
        </form>
      )}
    </details>
  );
}
