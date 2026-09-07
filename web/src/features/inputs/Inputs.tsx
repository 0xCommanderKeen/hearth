import { useEffect, useRef, useState } from "react";
import {
  Client,
  RequestError,
  type SyntheticInput,
  type InputChange,
} from "../../shared/client";
import "./inputs.css";

function inputRoute() {
  try {
    return window.location.hash.startsWith("#inputs/")
      ? decodeURIComponent(window.location.hash.slice(8))
      : "";
  } catch {
    return "invalid-link";
  }
}

export function InputLibrary({
  client,
  readOnly,
}: {
  client: Client;
  readOnly: boolean;
}) {
  const [route, setRoute] = useState(inputRoute);
  const [items, setItems] = useState<SyntheticInput[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const update = () => setRoute(inputRoute());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  useEffect(() => {
    let live = true;
    setItems(null);
    setError("");
    client
      .inputSets()
      .then((rows) => {
        if (live) setItems(rows);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [client, route, reload]);
  if (route) {
    const [id, rawRevision] = route.split("/");
    return (
      <InputEditor
        key={route}
        client={client}
        id={id}
        revision={rawRevision ? Number(rawRevision) : undefined}
        readOnly={readOnly}
      />
    );
  }
  const filtered = items?.filter((item) =>
    item.name.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <section className="input-library" aria-label="Input library">
      <div className="input-heading">
        <div>
          <span className="eyebrow">FICTIONAL NOTES · EXPLICIT ACCESS</span>
          <h2>Give each resident its own reading.</h2>
          <p>
            Small, named sets of fictional facts. Choose who receives them;
            inspect what every run read.
          </p>
        </div>
        {!readOnly && (
          <a className="input-add" href="#inputs/new">
            Add input set ＋
          </a>
        )}
      </div>
      <label>
        Find an input set
        <input
          value={query}
          maxLength={120}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Orchard, harbor, project notes…"
        />
      </label>
      {error ? (
        <p role="alert">
          {error}{" "}
          <button onClick={() => setReload(reload + 1)}>
            Retry input library
          </button>
        </p>
      ) : !items ? (
        <p role="status">Loading inputs…</p>
      ) : !filtered?.length ? (
        <div className="empty">
          <h3>No input sets here yet.</h3>
          <p>
            Add fictional notes, then select them on a resident’s profile.
            Unconfigured residents receive no notes.
          </p>
        </div>
      ) : (
        <div className="input-shelves">
          {filtered.map((item) => (
            <a
              className="input-card"
              key={item.input_set_id}
              href={`#inputs/${encodeURIComponent(item.input_set_id)}`}
            >
              <span className="eyebrow">REV {item.revision}</span>
              <h3>{item.name}</h3>
              <p>{item.notes[0] ?? "An empty note set."}</p>
              <small>
                {item.notes.length} {item.notes.length === 1 ? "note" : "notes"}{" "}
                · Read & edit →
              </small>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}

function InputEditor({
  client,
  id,
  revision,
  readOnly,
}: {
  client: Client;
  id: string;
  revision?: number;
  readOnly: boolean;
}) {
  const [saved, setSaved] = useState<SyntheticInput | null>(null);
  const [name, setName] = useState("");
  const [notes, setNotes] = useState<string[]>([""]);
  const [loading, setLoading] = useState(id !== "new");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [retry, setRetry] = useState(false);
  const [conflict, setConflict] = useState(false);
  const pending = useRef<InputChange | null>(null);
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
      const result = await client.inputSet(id, revision);
      if (!live.current) return;
      setSaved(result);
      setName(result.name);
      setNotes(result.notes.length ? result.notes : [""]);
      setConflict(false);
    } catch (e) {
      if (live.current)
        setError(
          e instanceof Error ? e.message : "Could not read this input set.",
        );
    } finally {
      if (live.current) setLoading(false);
    }
  }
  useEffect(() => {
    if (id !== "new") void load();
  }, [client, id, revision]);
  async function save() {
    if (busy || readOnly || revision || conflict) return;
    pending.current ??= {
      command_id: crypto.randomUUID(),
      input_set_id: saved?.input_set_id,
      expected_revision: saved?.revision,
      name,
      notes: notes.filter((note) => note !== ""),
    };
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await client.saveInput(pending.current);
      if (!live.current) return;
      pending.current = null;
      setRetry(false);
      setNotice(
        `Saved input revision ${result.revision}. Future admissions use these notes.`,
      );
      if (id === "new")
        window.location.hash = `#inputs/${encodeURIComponent(result.input_set_id)}`;
      else await load();
    } catch (e) {
      if (!live.current) return;
      if (
        e instanceof RequestError &&
        [401, 404, 409, 413, 422].includes(e.status)
      ) {
        pending.current = null;
        setRetry(false);
        setConflict(e.status === 409);
        setError(
          e.status === 409
            ? "This input set changed. Your draft is retained. Load its current revision before saving again."
            : e.message,
        );
      } else {
        setRetry(true);
        setError(
          "Save is unconfirmed. Retry the exact notes to recover the operation receipt.",
        );
      }
    } finally {
      if (live.current) setBusy(false);
    }
  }
  const locked =
    busy || readOnly || revision !== undefined || retry || conflict;
  return (
    <section className="input-editor" aria-label="Input detail">
      <a href="#inputs">← All inputs</a>
      <div className="input-heading">
        <div>
          <span className="eyebrow">
            FICTIONAL SOURCE DATA{saved ? ` · REVISION ${saved.revision}` : ""}
          </span>
          <h2>{saved?.name ?? "Write a few fictional facts."}</h2>
          <p>
            Notes provide facts to read. They grant no tools or management
            permissions.
          </p>
        </div>
      </div>
      {error && (
        <p className="notice error" role="alert">
          {error.replaceAll("_", " ")}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {revision !== undefined && (
        <p className="notice">
          Historical revision {revision}. This is the exact input linked from
          the run.{" "}
          <a href={`#inputs/${encodeURIComponent(id)}`}>
            Open the current set →
          </a>
        </p>
      )}
      {loading ? (
        <p role="status">Loading input revision…</p>
      ) : id !== "new" && !saved ? (
        <button onClick={() => void load()}>Retry input revision</button>
      ) : (
        <>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <fieldset disabled={locked}>
              <label>
                Input set name
                <input
                  required
                  maxLength={120}
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value);
                    setNotice("");
                  }}
                />
              </label>
              <div className="input-notebook">
                {notes.map((note, index) => (
                  <div className="input-note" key={index}>
                    <label>
                      {index === 0 ? "Notes" : `Note ${index + 1}`}
                      <textarea
                        maxLength={4000}
                        value={note}
                        onChange={(e) => {
                          setNotes(
                            notes.map((old, i) =>
                              i === index ? e.target.value : old,
                            ),
                          );
                          setNotice("");
                        }}
                        placeholder="For example: the fictional harbor welcomed three boats today."
                      />
                    </label>
                    {notes.length > 1 && (
                      <button
                        type="button"
                        className="quiet"
                        onClick={() =>
                          setNotes(notes.filter((_, i) => i !== index))
                        }
                      >
                        Remove note {index + 1}
                      </button>
                    )}
                  </div>
                ))}
              </div>
              <button
                type="button"
                className="quiet"
                disabled={locked || notes.length >= 32}
                onClick={() => setNotes([...notes, ""])}
              >
                Add another note
              </button>
              <p className="muted">
                Up to 32 notes, 4,000 characters each and 32 KiB per set. Clear
                all notes to save an explicit empty set.
              </p>
            </fieldset>
            {revision === undefined && (
              <button className="primary" disabled={locked}>
                {saved ? "Save input revision" : "Create input set"}
              </button>
            )}
          </form>
          {retry && (
            <button disabled={busy || readOnly} onClick={() => void save()}>
              Retry pending input save
            </button>
          )}
          {conflict && (
            <button disabled={busy} onClick={() => void load()}>
              Load current input revision (replace draft)
            </button>
          )}
          {saved && (
            <p className="muted">
              Created by {saved.created_by} · edited{" "}
              {new Date(saved.edited_at * 1000).toLocaleString()}. Existing runs
              keep their pinned revision.
            </p>
          )}
        </>
      )}
    </section>
  );
}
