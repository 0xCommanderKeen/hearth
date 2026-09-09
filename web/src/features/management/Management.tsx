import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Client,
  type ManagementCatalog,
  type ManagementChange,
  type ManagementGrant,
  type ManagementCapability,
} from "../../shared/client";
import "./management.css";

function selectedFromHash() {
  try {
    return location.hash.startsWith("#management/")
      ? decodeURIComponent(location.hash.slice(12))
      : "";
  } catch {
    return "";
  }
}
export function ManagementPanel({
  client,
  readOnly,
  onChanged,
}: {
  client: Client;
  readOnly: boolean;
  onChanged: () => void;
}) {
  const [catalog, setCatalog] = useState<ManagementCatalog | null>(null),
    [error, setError] = useState(""),
    [selected, setSelected] = useState(selectedFromHash),
    [reload, setReload] = useState(0),
    [busy, setBusy] = useState(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    const update = () => setSelected(selectedFromHash());
    window.addEventListener("hashchange", update);
    return () => {
      live.current = false;
      window.removeEventListener("hashchange", update);
    };
  }, []);
  useEffect(() => {
    let current = true;
    setError("");
    client
      .managementCatalog()
      .then((value) => {
        if (current) setCatalog(value);
      })
      .catch((e) => {
        if (current) setError(e.message);
      });
    return () => {
      current = false;
    };
  }, [client, reload]);
  async function bootstrap() {
    setBusy(true);
    setError("");
    try {
      const result = await client.bootstrapManagement();
      if (!live.current) return;
      location.hash = `#management/${result.resident_id}`;
      setSelected(result.resident_id);
      setReload((x) => x + 1);
      onChanged();
    } catch (e) {
      if (live.current)
        setError(
          e instanceof Error
            ? e.message
            : "Setup failed. Retry the same setup.",
        );
    } finally {
      if (live.current) setBusy(false);
    }
  }
  const resident =
    catalog?.residents.find((row) => row.id === selected) ??
    catalog?.residents.find((row) => row.name === "Karen") ??
    catalog?.residents[0];
  return (
    <section
      className="management-panel"
      aria-label="Resident management policy"
    >
      <div className="management-intro">
        <div>
          <span className="eyebrow">OPERATOR AUTHORITY · BOUNDED ACTIONS</span>
          <h2>Let a resident build the household.</h2>
          <p>
            Give selected residents permission to create and assign work within
            explicit limits.
          </p>
        </div>
        {!readOnly && (
          <button
            className="primary"
            disabled={busy}
            onClick={() => void bootstrap()}
          >
            Set up Karen
          </button>
        )}
      </div>
      <p className="management-rule">
        New residents receive no management grant. Skills describe how to work;
        only these operator grants permit management actions. Revoking or
        editing a grant stops further tool calls from its active runs.
      </p>
      {error && <p role="alert">{error}</p>}
      {!catalog ? (
        <button disabled={busy} onClick={() => setReload((x) => x + 1)}>
          Reload management policy
        </button>
      ) : (
        <>
          {catalog.residents.length === 0 ? (
            <p>
              Set up Karen to add the Create residents skill and a bounded
              initial grant.
            </p>
          ) : (
            <>
              <label className="management-who">
                Whose management grant?
                <select
                  value={resident?.id ?? ""}
                  onChange={(e) => {
                    setSelected(e.target.value);
                    location.hash = `#management/${e.target.value}`;
                  }}
                >
                  {catalog.residents.map((row) => (
                    <option value={row.id} key={row.id}>
                      {row.name}
                      {row.grant.enabled
                        ? " · management enabled"
                        : " · no management"}
                    </option>
                  ))}
                </select>
              </label>
              {resident && (
                <GrantEditor
                  key={`${resident.id}:${resident.grant.revision}`}
                  client={client}
                  catalog={catalog}
                  grant={resident.grant}
                  name={resident.name}
                  readOnly={readOnly}
                  onSaved={() => {
                    setReload((x) => x + 1);
                    onChanged();
                  }}
                  onReload={() => setReload((x) => x + 1)}
                />
              )}
            </>
          )}
          {!!catalog.operations.length && (
            <section
              className="management-history"
              aria-label="Management operation history"
            >
              <span className="eyebrow">DURABLE OPERATION RECEIPTS</span>
              <h3>Created and assigned work</h3>
              {catalog.operations.map((item) => (
                <article key={`${item.actor}:${item.operation_id}`}>
                  <a href={item.resident_link}>Open resident →</a>
                  <p>
                    {item.status} · {item.operation_id}
                  </p>
                  <small>
                    Originating run{" "}
                    <a href={`#run-${item.originating_run_id}`}>
                      {item.originating_run_id}
                    </a>
                    {item.task_id && <> · Task {item.task_id}</>}
                  </small>
                </article>
              ))}
            </section>
          )}
        </>
      )}
    </section>
  );
}
const capabilityLabels: Record<ManagementCapability, string> = {
  create_residents: "Create residents",
  assign_work: "Assign and start managed work",
  routines: "Create daily routines",
  author_skills: "Author skills and run bounded examples",
  assign_skills: "Assign exact skills to managed residents",
  update_residents: "Edit managed configurations",
  manage_lifecycle: "Pause, resume and archive managed residents",
  writable_memory: "Let provisioned residents write their own memory",
};
function GrantEditor({
  client,
  catalog,
  grant,
  name,
  readOnly,
  onSaved,
  onReload,
}: {
  client: Client;
  catalog: ManagementCatalog;
  grant: ManagementGrant;
  name: string;
  readOnly: boolean;
  onSaved: () => void;
  onReload: () => void;
}) {
  const { resident_id, revision, ...initial } = grant;
  const [draft, setDraft] = useState<ManagementChange>({
      ...initial,
      expected_revision: revision,
    }),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const update = <K extends keyof ManagementChange>(
    key: K,
    value: ManagementChange[K],
  ) => setDraft((old) => ({ ...old, [key]: value }));
  async function save(event: FormEvent) {
    event.preventDefault();
    if (readOnly || busy) return;
    setBusy(true);
    setError("");
    try {
      await client.saveManagement(resident_id, draft);
      if (live.current) onSaved();
    } catch (e) {
      if (live.current)
        setError(
          `${e instanceof Error ? e.message : "Save failed"}. Your draft is preserved. Reload the current grant before applying further changes.`,
        );
    } finally {
      if (live.current) setBusy(false);
    }
  }
  return (
    <form
      className="management-grant"
      onSubmit={(e) => void save(e)}
      aria-label={`${name} management grant`}
    >
      <div className="section-title">
        <div>
          <span className="eyebrow">GRANT · REVISION {revision}</span>
          <h3>{name}</h3>
        </div>
        <a href={`#residents/${resident_id}`}>Open resident →</a>
      </div>
      {error && (
        <p role="alert">
          {error}{" "}
          <button type="button" onClick={onReload}>
            Reload current grant
          </button>
        </p>
      )}
      <fieldset disabled={readOnly || busy}>
        <label className="management-choice management-enabled">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => update("enabled", e.target.checked)}
          />
          <span>Enable management tools</span>
        </label>
        <div className="management-columns">
          <div>
            <h4>Permitted actions</h4>
            {(Object.keys(capabilityLabels) as ManagementCapability[]).map(
              (key) => (
                <label key={key} className="management-choice">
                  <input
                    type="checkbox"
                    checked={draft.capabilities.includes(key)}
                    onChange={(e) =>
                      update(
                        "capabilities",
                        e.target.checked
                          ? [...draft.capabilities, key]
                          : draft.capabilities.filter((value) => value !== key),
                      )
                    }
                  />
                  <span>{capabilityLabels[key]}</span>
                </label>
              ),
            )}
            <h4>Execution profiles</h4>
            {catalog.profiles.map((profile) => (
              <label key={profile.id} className="management-choice">
                <input
                  type="checkbox"
                  checked={draft.profiles.includes(profile.id)}
                  onChange={(e) =>
                    update(
                      "profiles",
                      e.target.checked
                        ? [...draft.profiles, profile.id]
                        : draft.profiles.filter(
                            (value) => value !== profile.id,
                          ),
                    )
                  }
                />
                {/* Named by the server's own registry: this view knows what runtimes
                    a household has, never what any one of them is called. */}
                <span>{profile.name}</span>
              </label>
            ))}
          </div>
          <div>
            <h4>Permitted inputs</h4>
            {catalog.input_sets.length ? (
              catalog.input_sets.map((item) => (
                <label key={item.input_set_id} className="management-choice">
                  <input
                    type="checkbox"
                    checked={draft.input_set_ids.includes(item.input_set_id)}
                    onChange={(e) =>
                      update(
                        "input_set_ids",
                        e.target.checked
                          ? [...draft.input_set_ids, item.input_set_id]
                          : draft.input_set_ids.filter(
                              (value) => value !== item.input_set_id,
                            ),
                      )
                    }
                  />
                  <span>
                    {item.name}
                    <small>Latest revision at admission</small>
                  </span>
                </label>
              ))
            ) : (
              <p>
                No named inputs yet. <a href="#inputs">Create input sets →</a>
              </p>
            )}
            <p className="muted">
              An empty selection permits residents with no inputs. Existing
              library skills remain available for inspection and exact
              assignment.
            </p>
          </div>
        </div>
        <div className="management-limits">
          <label>
            Maximum managed residents
            <input
              type="number"
              required
              min="0"
              max="20"
              value={draft.max_residents}
              onChange={(e) => update("max_residents", Number(e.target.value))}
            />
          </label>
          <label>
            Maximum resident daily limit (USD)
            <input
              type="number"
              required
              min="0"
              max="10"
              step="0.000001"
              value={draft.max_daily_limit / 1000000}
              onChange={(e) =>
                update(
                  "max_daily_limit",
                  Math.round(Number(e.target.value) * 1000000),
                )
              }
            />
          </label>
          <label>
            Maximum work reservation (USD)
            <input
              type="number"
              required
              min="0.000001"
              max="2"
              step="0.000001"
              value={draft.max_reserve / 1000000}
              onChange={(e) =>
                update(
                  "max_reserve",
                  Math.round(Number(e.target.value) * 1000000),
                )
              }
            />
          </label>
          <label>
            Maximum tool calls per run
            <input
              type="number"
              required
              min="1"
              max="64"
              value={draft.max_calls}
              onChange={(e) => update("max_calls", Number(e.target.value))}
            />
          </label>
        </div>
      </fieldset>
      <p className="muted">
        The shared household allowance, resident limits and concurrency still
        apply. Management runs last at most ten minutes. Subscription spending
        is an API-equivalent estimate.
      </p>
      <button className="primary" disabled={readOnly || busy}>
        Save management grant
      </button>
    </form>
  );
}
