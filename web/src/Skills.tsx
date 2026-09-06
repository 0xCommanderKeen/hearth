import { useState } from "react";
import { Client, type Resident, type ResidentDeclaration } from "./client";

export function Skills({
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
  const [saved, setSaved] = useState<ResidentDeclaration | null>(null);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState("");
  const changed = saved !== null && saved.revision !== resident.revision;
  return (
    <details className="skill-editor">
      <summary>{resident.name} · Skill text</summary>
      <p>
        Markdown instructions that travel with each resident revision. They do
        not grant access to files, tools or external actions.
      </p>
      <button
        disabled={busy}
        onClick={() =>
          void act(async () => {
            const current = await client.resident(resident.id);
            setSaved(current);
            setDraft(current.declaration.skill_text);
            setNotice("");
          })
        }
      >
        {saved ? "Load current revision (replace draft)" : "Read skill text"}
      </button>
      {saved && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (busy || readOnly || changed) return;
            void act(async () => {
              const result = await client.saveResident(saved, draft);
              setSaved(result);
              setNotice(`Saved skill text at revision ${result.revision}.`);
            });
          }}
        >
          <label htmlFor={`skills-${resident.id}`}>
            Skill text for {resident.name}
          </label>
          <textarea
            id={`skills-${resident.id}`}
            value={draft}
            maxLength={32000}
            disabled={busy || readOnly}
            onChange={(event) => {
              setDraft(event.target.value);
              setNotice("");
            }}
          />
          <small>
            Editing revision {saved.revision}. Changes apply to newly admitted
            runs. Existing work keeps its pinned revision.
          </small>
          {changed && (
            <p role="status">
              The resident changed while this draft was open. Your draft is
              retained. Load the current revision before saving again.
            </p>
          )}
          {readOnly && <p>This restored copy is read-only.</p>}
          <button
            disabled={
              busy ||
              readOnly ||
              changed ||
              draft === saved.declaration.skill_text
            }
          >
            Save skill text
          </button>
          {notice && <p role="status">{notice}</p>}
        </form>
      )}
    </details>
  );
}
