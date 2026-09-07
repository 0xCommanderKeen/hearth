import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Client,
  RequestError,
  type CatalogSkill,
  type SkillChange,
  type SkillDraft,
} from "../../shared/client";
import { SkillUsers } from "./SkillUsers";
import { Markdown } from "./Markdown";
import { SkillEvidence } from "./SkillEvidence";
import { SkillExamples, cleanExamples, exampleTemplate } from "./SkillExamples";
import "./skills.css";

const empty: SkillDraft = { name: "", description: "", instructions: "" };
const stamp = (at: number) => new Date(at * 1000).toLocaleString();
const editable = (skill: CatalogSkill): SkillDraft => ({
  name: skill.name,
  description: skill.description,
  instructions: skill.instructions,
  ...(skill.authoring
    ? { authoring: { examples: skill.authoring.examples } }
    : {}),
});
function route() {
  try {
    return window.location.hash.startsWith("#skills/")
      ? decodeURIComponent(window.location.hash.slice(8))
      : "";
  } catch {
    return "invalid-link";
  }
}
export function SkillCatalog({
  client,
  readOnly,
}: {
  client: Client;
  readOnly: boolean;
}) {
  const [selected, setSelected] = useState(route);
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const [skills, setSkills] = useState<CatalogSkill[] | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const update = () => setSelected(route());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  useEffect(() => {
    let live = true;
    setSkills(null);
    setError("");
    let fetching = false;
    const refresh = () => {
      if (fetching) return;
      fetching = true;
      void client
        .skills(query, archived)
        .then((rows) => {
          if (live) {
            setSkills(rows);
            setError("");
          }
        })
        .catch((e) => {
          if (live) setError(e.message);
        })
        .finally(() => {
          fetching = false;
        });
    };
    refresh();
    const timer = selected ? undefined : window.setInterval(refresh, 1000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [client, query, archived, reload, selected]);
  if (selected)
    return (
      <SkillEditor
        key={selected}
        client={client}
        id={selected}
        readOnly={readOnly}
      />
    );
  return (
    <section className="skill-catalog" aria-label="Shared skill library">
      <div className="skill-library-heading">
        <div>
          <span className="eyebrow">SHARED KNOW-HOW</span>
          <h2>A library for useful work.</h2>
          <p>
            Reusable instructions for your residents. Skills guide work;
            permissions are set separately.
          </p>
        </div>
        {!readOnly && (
          <a className="skill-add" href="#skills/new">
            Add skill ＋
          </a>
        )}
      </div>
      <div className="skill-toolbar">
        <label>
          Search skills
          <input
            value={query}
            maxLength={200}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Name or usage guidance"
          />
        </label>
        <label className="skill-checkbox">
          <input
            type="checkbox"
            checked={archived}
            onChange={(e) => setArchived(e.target.checked)}
          />{" "}
          Include archived
        </label>
      </div>
      {error ? (
        <div role="alert" className="notice error">
          {error}{" "}
          <button onClick={() => setReload(reload + 1)}>
            Retry loading skills
          </button>
        </div>
      ) : skills === null ? (
        <p role="status">Loading skills…</p>
      ) : !skills.length ? (
        <div className="empty">
          <h3>
            {query ? "No matching skills." : "Your shared library starts here."}
          </h3>
          <p>
            {query
              ? "Try another name or include archived skills."
              : "Add instructions once, then reuse them across residents."}
          </p>
        </div>
      ) : (
        <div className="skill-list">
          {skills.map((skill) => (
            <a
              href={`#skills/${encodeURIComponent(skill.skill_id)}`}
              className="skill-card"
              key={skill.skill_id}
            >
              <div>
                <span className="eyebrow">
                  REV {skill.revision} · {skill.status.toUpperCase()}
                </span>
                <h3>{skill.name}</h3>
                <p>{skill.description}</p>
                {skill.authoring && (
                  <small>
                    By {skill.created_by_name ?? skill.created_by} ·{" "}
                    {skill.authoring.validation?.status ??
                      "awaiting executed checks"}
                  </small>
                )}
              </div>
              <span className="profile-link">Read skill →</span>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}

function SkillEditor({
  client,
  id,
  readOnly,
}: {
  client: Client;
  id: string;
  readOnly: boolean;
}) {
  const [saved, setSaved] = useState<CatalogSkill | null>(null);
  const [draft, setDraft] = useState<SkillDraft>(empty);
  const [history, setHistory] = useState<CatalogSkill[]>([]);
  const [prior, setPrior] = useState<CatalogSkill | null>(null);
  const [loading, setLoading] = useState(id !== "new");
  const [busy, setBusy] = useState(false);
  const [validationBusy, setValidationBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [retry, setRetry] = useState(false);
  const pending = useRef<SkillChange | null>(null);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  async function load() {
    setLoading(true);
    setError("");
    try {
      const [skill, revisions] = await Promise.all([
        client.skill(id),
        client.skillHistory(id),
      ]);
      if (!live.current) return;
      setSaved(skill);
      setDraft(editable(skill));
      setHistory(revisions);
      setPrior(null);
      setConflict(false);
    } catch (e) {
      if (live.current)
        setError(e instanceof Error ? e.message : "Could not load skill.");
    } finally {
      if (live.current) setLoading(false);
    }
  }
  useEffect(() => {
    if (id !== "new") void load();
  }, [client, id]);
  async function change(archive = false) {
    if (busy || validationBusy || readOnly || conflict) return;
    if (!pending.current)
      pending.current = {
        command_id: crypto.randomUUID(),
        ...(saved
          ? { skill_id: saved.skill_id, expected_revision: saved.revision }
          : {}),
        ...(archive
          ? {}
          : {
              content: {
                ...draft,
                ...(draft.authoring
                  ? { authoring: cleanExamples(draft.authoring) }
                  : {}),
              },
            }),
      };
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const receipt = await client.changeSkill(pending.current);
      if (!live.current) return;
      pending.current = null;
      setRetry(false);
      setNotice(
        `Saved revision ${receipt.revision}. Receipt ${receipt.command_id}.`,
      );
      if (id === "new") {
        window.location.hash = `#skills/${encodeURIComponent(receipt.skill_id)}`;
        return;
      }
      await load();
    } catch (e) {
      if (!live.current) return;
      if (
        e instanceof RequestError &&
        [401, 404, 409, 422].includes(e.status)
      ) {
        pending.current = null;
        setRetry(false);
        if (e.status === 409) setConflict(true);
        setError(
          e.status === 409
            ? "This skill changed or is no longer editable. Your draft is retained. Load the current revision before saving again."
            : e.message,
        );
      } else {
        setRetry(true);
        setError(
          "Save is unconfirmed. Your exact draft and operation are retained. Retry to recover the receipt.",
        );
      }
    } finally {
      if (live.current) setBusy(false);
    }
  }
  const disabled =
    busy ||
    validationBusy ||
    retry ||
    readOnly ||
    conflict ||
    saved?.status === "archived";
  const update = (
    field: "name" | "description" | "instructions",
    value: string,
  ) => {
    setDraft({ ...draft, [field]: value });
    setNotice("");
  };
  const dirty =
    !!saved &&
    (draft.name !== saved.name ||
      draft.description !== saved.description ||
      draft.instructions !== saved.instructions ||
      JSON.stringify(draft.authoring ?? null) !==
        JSON.stringify(
          saved.authoring ? { examples: saved.authoring.examples } : null,
        ));
  useEffect(() => {
    if (!saved || loading || busy || validationBusy || retry || conflict)
      return;
    let active = true;
    let fetching = false;
    const timer = window.setInterval(() => {
      if (fetching) return;
      fetching = true;
      void client
        .skill(id)
        .then(async (current) => {
          if (!active || current.revision === saved.revision) return;
          if (dirty) {
            setConflict(true);
            setError(
              "A newer revision is available. Your draft is retained. Load the current revision before saving again.",
            );
            return;
          }
          const revisions = await client.skillHistory(id);
          if (!active) return;
          setSaved(current);
          setDraft(editable(current));
          setHistory(revisions);
        })
        .catch(() => {
          /* The next refresh retries; preserve the current editor. */
        })
        .finally(() => {
          fetching = false;
        });
    }, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [
    client,
    id,
    saved,
    draft,
    dirty,
    loading,
    busy,
    validationBusy,
    retry,
    conflict,
  ]);
  return (
    <section className="skill-detail" aria-label="Skill detail">
      {!busy && !validationBusy && !retry && (
        <a href="#skills" className="back-link">
          ← All skills
        </a>
      )}
      <div className="skill-library-heading">
        <div>
          <span className="eyebrow">
            {saved
              ? `REVISION ${saved.revision} · ${saved.status.toUpperCase()}`
              : "NEW SHARED SKILL"}
          </span>
          <h2>{saved?.name ?? "Write a skill."}</h2>
        </div>
        {saved && (
          <small>
            Created by {saved.created_by_name ?? saved.created_by} ·{" "}
            {stamp(saved.created_at)}
            <br />
            Edited by {saved.edited_by_name ?? saved.edited_by} ·{" "}
            {stamp(saved.edited_at)}
          </small>
        )}
      </div>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {loading ? (
        <p role="status">Loading skill…</p>
      ) : id !== "new" && !saved ? (
        <button onClick={() => void load()}>Retry loading skill</button>
      ) : (
        <>
          {saved?.status === "archived" && (
            <p className="notice">
              Archived skills remain available for inspection and history. They
              cannot be newly assigned.
            </p>
          )}
          {saved?.authoring && (
            <SkillEvidence
              key={`${saved.skill_id}:${saved.revision}`}
              client={client}
              skill={saved}
              readOnly={readOnly || busy || retry || conflict}
              dirty={dirty}
              onPublished={() => void load()}
              onPendingChange={setValidationBusy}
            />
          )}
          {conflict && (
            <button disabled={busy || readOnly} onClick={() => void load()}>
              Load current revision (replace draft)
            </button>
          )}
          <form
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              void change();
            }}
          >
            <fieldset disabled={disabled}>
              {saved?.authoring ? (
                <p className="notice">
                  Edits create a new draft and need fresh executed examples
                  before activation. Existing resident assignments keep their
                  exact revisions.
                </p>
              ) : (
                <label className="skill-checkbox">
                  <input
                    type="checkbox"
                    checked={!!draft.authoring}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        authoring: e.target.checked
                          ? exampleTemplate()
                          : undefined,
                      })
                    }
                  />
                  Require executed examples before activation
                </label>
              )}
              <label htmlFor="skill-name">Skill name</label>
              <input
                id="skill-name"
                required
                maxLength={120}
                value={draft.name}
                onChange={(e) => update("name", e.target.value)}
              />
              <label htmlFor="skill-description">
                Description / when to use
              </label>
              <textarea
                id="skill-description"
                required
                maxLength={2000}
                value={draft.description}
                onChange={(e) => update("description", e.target.value)}
              />
              <div className="skill-writing">
                <div>
                  <label htmlFor="skill-instructions">
                    Markdown instructions
                  </label>
                  <textarea
                    id="skill-instructions"
                    required
                    maxLength={32000}
                    value={draft.instructions}
                    onChange={(e) => update("instructions", e.target.value)}
                  />
                  <small>
                    {draft.instructions.length.toLocaleString()} / 32,000
                    characters
                  </small>
                </div>
                <section
                  className="skill-preview"
                  aria-label="Markdown preview"
                >
                  <span className="eyebrow">PREVIEW</span>
                  <Markdown text={draft.instructions} />
                </section>
              </div>
              {draft.authoring && (
                <SkillExamples
                  value={draft.authoring}
                  onChange={(authoring) => setDraft({ ...draft, authoring })}
                />
              )}
            </fieldset>
            <div className="skill-actions">
              <button className="primary" disabled={disabled}>
                {saved ? "Save revision" : "Create skill"}
              </button>
              {saved && (
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => void change(true)}
                >
                  Archive skill
                </button>
              )}
            </div>
          </form>
          {retry && (
            <button disabled={busy || readOnly} onClick={() => void change()}>
              Retry pending save
            </button>
          )}
          {saved && <SkillUsers client={client} id={saved.skill_id} />}
          {saved && (
            <section
              className="skill-history"
              aria-label="Skill revision history"
            >
              <h3>Revision history</h3>
              <p className="muted">Every saved revision stays inspectable.</p>
              <div className="skill-revisions">
                {history.map((revision) => (
                  <button
                    key={revision.revision}
                    onClick={() => setPrior(revision)}
                    aria-pressed={prior?.revision === revision.revision}
                  >
                    Revision {revision.revision} · {revision.status}
                    <small>
                      {revision.edited_by_name ?? revision.edited_by} ·{" "}
                      {stamp(revision.edited_at)}
                    </small>
                  </button>
                ))}
              </div>
              {prior && (
                <article
                  aria-label={`Revision ${prior.revision} content`}
                  className="skill-preview"
                >
                  <h3>{prior.name}</h3>
                  <p>{prior.description}</p>
                  <Markdown text={prior.instructions} />
                  {prior.authoring && (
                    <SkillEvidence
                      key={`history:${prior.revision}`}
                      client={client}
                      skill={prior}
                      readOnly={true}
                      dirty={false}
                      onPublished={() => {}}
                    />
                  )}
                </article>
              )}
            </section>
          )}
        </>
      )}
    </section>
  );
}
