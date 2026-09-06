import { useEffect, useRef, useState } from "react";
import {
  Client,
  RequestError,
  type AssignedSkill,
  type AssignmentSet,
  type AssignmentChange,
  type CatalogSkill,
} from "../../shared/client";

function RevisionChoice({
  client,
  skill,
  disabled,
  onChange,
}: {
  client: Client;
  skill: AssignedSkill;
  disabled: boolean;
  onChange: (skill: AssignedSkill) => void;
}) {
  const [history, setHistory] = useState<CatalogSkill[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    client
      .skillHistory(skill.skill_id)
      .then((rows) => {
        if (live) setHistory(rows);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [client, skill.skill_id]);
  return (
    <label>
      Assigned revision for {skill.name}
      <select
        disabled={
          disabled || skill.catalog_status === "archived" || !history.length
        }
        value={skill.revision}
        onChange={(e) => {
          const revision = history.find(
            (row) => row.revision === Number(e.target.value),
          );
          if (revision)
            onChange({
              ...revision,
              latest_revision: skill.latest_revision,
              catalog_status: skill.catalog_status,
            });
        }}
      >
        {history.length ? (
          history
            .filter((row) => row.status === "active")
            .map((row) => (
              <option key={row.revision} value={row.revision}>
                Revision {row.revision}
                {row.revision === skill.latest_revision ? " · latest" : ""}
              </option>
            ))
        ) : (
          <option value={skill.revision}>Revision {skill.revision}</option>
        )}
      </select>
      {error && <small role="alert">Could not load revisions: {error}</small>}
    </label>
  );
}
export function Assignments({
  client,
  residentId,
  readOnly,
}: {
  client: Client;
  residentId: string;
  readOnly: boolean;
}) {
  const [saved, setSaved] = useState<AssignmentSet | null>(null);
  const [draft, setDraft] = useState<AssignedSkill[]>([]);
  const [catalog, setCatalog] = useState<CatalogSkill[]>([]);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [retry, setRetry] = useState(false);
  const pending = useRef<AssignmentChange | null>(null);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  async function load() {
    setBusy(true);
    setError("");
    try {
      const [current, skills] = await Promise.all([
        client.assignments(residentId),
        client.skills("", true),
      ]);
      if (!live.current) return;
      setSaved(current);
      setDraft(current.skills);
      setCatalog(skills);
      setConflict(false);
    } catch (e) {
      if (live.current)
        setError(
          e instanceof Error ? e.message : "Could not read assignments.",
        );
    } finally {
      if (live.current) setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, [client, residentId]);
  async function save() {
    if (!saved || busy || readOnly || conflict) return;
    pending.current ??= {
      resident_id: residentId,
      command_id: crypto.randomUUID(),
      expected_revision: saved.revision,
      skills: draft.map(({ skill_id, revision }) => ({ skill_id, revision })),
    };
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const receipt = await client.saveAssignments(pending.current);
      if (!live.current) return;
      pending.current = null;
      setRetry(false);
      setNotice(
        `Saved assignment set ${receipt.revision}. Future runs use this order.`,
      );
      await load();
    } catch (e) {
      if (!live.current) return;
      if (
        e instanceof RequestError &&
        [401, 404, 409, 422].includes(e.status)
      ) {
        pending.current = null;
        setRetry(false);
        setConflict(e.status === 409);
        setError(
          e.status === 409
            ? "Assignments changed or a skill is unavailable. Your assignment draft is retained. Load current assignments before saving again."
            : e.message,
        );
      } else {
        setRetry(true);
        setError(
          "Save is unconfirmed. Retry the exact pending assignment set to recover its receipt.",
        );
      }
    } finally {
      if (live.current) setBusy(false);
    }
  }
  const disabled = busy || readOnly || conflict || retry;
  const available = catalog.filter(
    (skill) =>
      skill.status === "active" &&
      !draft.some((entry) => entry.skill_id === skill.skill_id),
  );
  function move(index: number, delta: number) {
    const next = [...draft];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    setDraft(next);
    setNotice("");
  }
  return (
    <section
      className="assignment-editor"
      aria-label="Assigned reusable skills"
    >
      <h3>Reusable skills</h3>
      <p>
        Exact revisions, applied from top to bottom. Publishing a new revision
        never changes this set automatically.
      </p>
      {error && (
        <p role="alert" className="notice error">
          {error.replaceAll("_", " ")}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {!saved ? (
        busy ? (
          <p>Loading assignments…</p>
        ) : (
          <button onClick={() => void load()}>Retry loading assignments</button>
        )
      ) : (
        <>
          {!draft.length && (
            <p className="muted">No reusable skills assigned.</p>
          )}
          <ol className="assigned-skills">
            {draft.map((skill, index) => (
              <li key={skill.skill_id}>
                <a href={`#skills/${encodeURIComponent(skill.skill_id)}`}>
                  {skill.name}
                </a>
                <p>{skill.description}</p>
                <small>
                  Revision {skill.revision}
                  {skill.catalog_status === "archived"
                    ? " · archived in catalog; retained assignment"
                    : skill.latest_revision &&
                        skill.latest_revision !== skill.revision
                      ? ` · revision ${skill.latest_revision} available`
                      : ""}
                </small>
                <RevisionChoice
                  client={client}
                  skill={skill}
                  disabled={disabled}
                  onChange={(next) => {
                    setDraft(draft.map((old, i) => (i === index ? next : old)));
                    setNotice("");
                  }}
                />
                <div className="skill-actions">
                  <button
                    disabled={disabled || index === 0}
                    aria-label={`Move ${skill.name} up`}
                    onClick={() => move(index, -1)}
                  >
                    ↑
                  </button>
                  <button
                    disabled={disabled || index === draft.length - 1}
                    aria-label={`Move ${skill.name} down`}
                    onClick={() => move(index, 1)}
                  >
                    ↓
                  </button>
                  <button
                    disabled={disabled}
                    onClick={() => {
                      setDraft(draft.filter((_, i) => i !== index));
                      setNotice("");
                    }}
                  >
                    Detach {skill.name}
                  </button>
                </div>
              </li>
            ))}
          </ol>
          <label>
            Attach a shared skill
            <select
              disabled={disabled || draft.length >= 8}
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">Choose a skill…</option>
              {available.map((skill) => (
                <option key={skill.skill_id} value={skill.skill_id}>
                  {skill.name} · revision {skill.revision}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={disabled || !selected || draft.length >= 8}
            onClick={() => {
              const skill = available.find((row) => row.skill_id === selected);
              if (skill)
                setDraft([
                  ...draft,
                  {
                    ...skill,
                    latest_revision: skill.revision,
                    catalog_status: skill.status,
                  },
                ]);
              setSelected("");
              setNotice("");
            }}
          >
            Attach selected skill
          </button>
          <p>
            <small>
              Up to 8 skills. Instructions grant no tools, budget or other
              permissions. <a href="#skills">Open the shared library →</a>
            </small>
          </p>
          <button
            disabled={disabled}
            className="primary"
            onClick={() => void save()}
          >
            Save assignments
          </button>
          {retry && (
            <button disabled={busy || readOnly} onClick={() => void save()}>
              Retry pending assignment save
            </button>
          )}
          {conflict && (
            <button disabled={busy} onClick={() => void load()}>
              Load current assignments (replace draft)
            </button>
          )}
        </>
      )}
    </section>
  );
}
