import { useEffect, useRef, useState } from "react";
import {
  Client,
  RequestError,
  type InputSelectionSet,
  type SyntheticInput,
  type InputProvenance,
} from "../../shared/client";
import "./inputs.css";

export function InputSelection({
  client,
  residentId,
  readOnly,
}: {
  client: Client;
  residentId: string;
  readOnly: boolean;
}) {
  const [saved, setSaved] = useState<InputSelectionSet | null>(null);
  const [catalog, setCatalog] = useState<SyntheticInput[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [retry, setRetry] = useState(false);
  const [conflict, setConflict] = useState(false);
  const pending = useRef<Parameters<Client["selectInputs"]>[0] | null>(null);
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
      const [selection, sets] = await Promise.all([
        client.inputSelection(residentId),
        client.inputSets(),
      ]);
      if (!live.current) return;
      setSaved(selection);
      setCatalog(sets);
      setChosen(selection.input_sets.map((item) => item.input_set_id));
      setConflict(false);
    } catch (e) {
      if (live.current)
        setError(
          e instanceof Error ? e.message : "Could not load input selection.",
        );
    } finally {
      if (live.current) setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, [client, residentId]);
  async function save() {
    if (busy || readOnly || conflict || !saved) return;
    pending.current ??= {
      resident_id: residentId,
      command_id: crypto.randomUUID(),
      expected_revision: saved.revision,
      input_sets: chosen.map((input_set_id) => ({ input_set_id })),
    };
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await client.selectInputs(pending.current);
      if (!live.current) return;
      pending.current = null;
      setRetry(false);
      setNotice(
        "Input selection saved. Future runs read the latest revision of these sets.",
      );
      await load();
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
            ? "Input selection changed. Your draft is retained; reload before saving again."
            : e.message,
        );
      } else {
        setRetry(true);
        setError(
          "Selection is unconfirmed. Retry the exact pending selection.",
        );
      }
    } finally {
      if (live.current) setBusy(false);
    }
  }
  const locked = busy || readOnly || retry || conflict;
  return (
    <section className="input-selection" aria-label="Resident inputs">
      <h3>Inputs</h3>
      <p>
        Choose up to four sets. Future admissions read their latest notes;
        admitted runs keep their exact revisions.
      </p>
      {error && <p role="alert">{error.replaceAll("_", " ")}</p>}
      {notice && <p role="status">{notice}</p>}
      {!saved ? (
        <button disabled={busy} onClick={() => void load()}>
          {busy ? "Loading inputs…" : "Retry input selection"}
        </button>
      ) : (
        <>
          {!chosen.length && (
            <p>No inputs selected. This resident receives no notes.</p>
          )}
          {catalog.map((item) => (
            <label key={item.input_set_id} className="input-choice">
              <input
                type="checkbox"
                disabled={
                  locked ||
                  (!chosen.includes(item.input_set_id) && chosen.length >= 4)
                }
                checked={chosen.includes(item.input_set_id)}
                onChange={(e) => {
                  setChosen(
                    e.target.checked
                      ? [...chosen, item.input_set_id]
                      : chosen.filter((id) => id !== item.input_set_id),
                  );
                  setNotice("");
                }}
              />
              <span>
                {item.name}
                <small>
                  Revision {item.revision} · {item.notes.length} notes
                </small>
              </span>
            </label>
          ))}
          {!!chosen.length && (
            <ol className="input-provenance" aria-label="Input reading order">
              {chosen.map((id) => (
                <li key={id}>
                  <a href={`#inputs/${encodeURIComponent(id)}`}>
                    {catalog.find((item) => item.input_set_id === id)?.name ??
                      id}
                  </a>
                </li>
              ))}
            </ol>
          )}
          <p>
            <a href="#inputs">Open the input library →</a>
          </p>
          <button
            className="primary"
            disabled={locked}
            onClick={() => void save()}
          >
            Save input selection
          </button>
          {retry && (
            <button disabled={busy || readOnly} onClick={() => void save()}>
              Retry pending selection
            </button>
          )}
          {conflict && (
            <button disabled={busy} onClick={() => void load()}>
              Load current selection (replace draft)
            </button>
          )}
        </>
      )}
    </section>
  );
}

export function RunInputs({ run }: { run: InputProvenance }) {
  if (run.inputs_error)
    return (
      <p className="notice error">
        Input provenance unavailable: {run.inputs_error.replaceAll("_", " ")}.
      </p>
    );
  if (run.input_state === "empty")
    return <small>Admitted with no inputs.</small>;
  if (!run.input_sets?.length) return null;
  return (
    <div aria-label="Inputs used by run">
      <small>Inputs used · in order</small>
      <ol className="input-provenance">
        {run.input_sets.map((input) => (
          <li key={input.input_set_id}>
            <a
              href={`#inputs/${encodeURIComponent(input.input_set_id)}/${input.revision}`}
            >
              {input.name}
            </a>{" "}
            · revision {input.revision}
          </li>
        ))}
      </ol>
    </div>
  );
}
