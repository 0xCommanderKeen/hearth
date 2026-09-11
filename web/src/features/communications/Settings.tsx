import { useEffect, useState } from "react";
import { DiscordSetup } from "./DiscordSetup";
import {
  Client,
  RequestError,
  type Resident,
  type Runtimes,
} from "../../shared/client";
import {
  api,
  time,
  words,
  type Config,
  type Status,
  type Scope,
  type Forwarding,
} from "./api";
export function CommunicationSettings({
  client,
  readOnly,
  residentId,
  residents = [],
  runtimes,
}: {
  client: Client;
  readOnly: boolean;
  residentId?: string;
  residents?: Resident[];
  runtimes?: Runtimes;
}) {
  const [data, setData] = useState<Status | null>(null);
  const [forwarding, setForwarding] = useState<Forwarding[]>([]);
  const [nextForwarding, setNextForwarding] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState<Config | null>(null);
  const [forwardEdit, setForwardEdit] = useState<Forwarding | null>(null);
  const [stopped, setStopped] = useState<Record<string, boolean>>({});
  const [probeRoutes, setProbeRoutes] = useState<Record<string, string>>({});
  async function load(after = "", forwardAfter = "") {
    setError("");
    setBusy(true);
    try {
      const [status, f] = await Promise.all([
        api(client).get<Status>(
          `?limit=100&after=${encodeURIComponent(after)}`,
        ),
        api(client).get<{ items: Forwarding[]; next_after: string | null }>(
          `/forwarding?limit=20&after=${encodeURIComponent(forwardAfter)}`,
        ),
      ]);
      setData((previous) =>
        after && previous
          ? {
              ...status,
              configuration: [
                ...previous.configuration,
                ...status.configuration,
              ],
            }
          : status,
      );
      setForwarding((previous) =>
        forwardAfter ? [...previous, ...f.items] : f.items,
      );
      setNextForwarding(f.next_after);
      setEdit(null);
      setForwardEdit(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, [client]);
  const configs =
    data?.configuration.filter((item): item is Config => item.value !== null) ??
    [];
  const probeable = (route: Config, connectionId: string) => {
    if (
      route.kind !== "route" ||
      route.value.connection_id !== connectionId ||
      route.value.state !== "active"
    )
      return false;
    const grant = configs.find(
      (item) => item.kind === "grant" && item.id === route.value.resident_id,
    );
    const address = route.value.address as Scope;
    return ["read", "listen", "reply", "post"].some(
      (key) =>
        Array.isArray(grant?.value[key]) &&
        (grant.value[key] as Scope[]).some(
          (scope) =>
            scope.connection_id === connectionId &&
            scope.guild_id === address.guild_id &&
            scope.channel_id === address.channel_id,
        ),
    );
  };
  const held = readOnly || !!data?.read_only;
  async function write(path: string, body: unknown, method = "POST") {
    if (held) return false;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await api(client).write(path, body, method);
      setMessage(`Action recorded. ${result ? JSON.stringify(result) : ""}`);
      await load();
      return true;
    } catch (e) {
      setError(
        e instanceof RequestError && e.status === 409
          ? `${e.message}. Reload settings to review the current revision; your edit has not been silently rebased.`
          : String(e),
      );
      return false;
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <h3>Settings & diagnostics</h3>
      <p>
        Only non-secret installation references belong here. Credentials are
        managed outside Hearth's declarations and repository. Reload reads
        cached local diagnostics and never contacts a transport. A probe
        explicitly requests a check; an active worker may poll or deliver
        independently.
      </p>
      <button disabled={busy} onClick={() => void load()}>
        Reload settings
      </button>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {held && (
        <p className="notice">
          Held copy: settings and diagnostics are read-only.
        </p>
      )}
      {(edit || forwardEdit) && (
        <button
          onClick={() => {
            setEdit(null);
            setForwardEdit(null);
          }}
        >
          Cancel editing
        </button>
      )}
      {data && (
        <>
          <DiscordSetup
            configs={configs}
            status={data}
            residents={residents}
            runtimes={runtimes}
            residentId={residentId}
            disabled={held || busy}
            onEdit={(item) => {
              setEdit(item);
              setForwardEdit(null);
            }}
            onSave={(kind, id, revision, value) =>
              write(
                `/configuration/${kind}/${encodeURIComponent(id)}`,
                { expected_revision: revision, value },
                "PUT",
              )
            }
          />
          <p>{data.retention}</p>
          <h4>Cached health</h4>
          <pre>{JSON.stringify(data.health, null, 2)}</pre>
          <h4>Scheduled checks</h4>
          {!data.schedule.length && <p>No retry schedule recorded.</p>}
          <ul>
            {data.schedule.map((item) => (
              <li key={`${item.kind}:${item.id}`}>
                {item.kind} {item.id} · eligible {time(item.eligible_at)} ·{" "}
                {item.error ? words(item.error) : "No error recorded"}
              </li>
            ))}
          </ul>
          <h4>Connections, routes & resident grants</h4>
          {data.configuration
            .filter((item) => item.value === null)
            .map((item) => (
              <p key={`${item.kind}:${item.id}`} role="alert">
                {item.kind} {item.id}: legacy configuration is too large for
                this bounded view. Editing is unavailable.
              </p>
            ))}
          {!data.configuration.length && (
            <p>
              No connections configured. New connections remain pending until
              explicitly configured and activated.
            </p>
          )}
          <ul className="communications-records">
            {configs.map((item) => (
              <li key={`${item.kind}:${item.id}`}>
                <strong>
                  {item.kind} · {String(item.value.label || item.id)}
                </strong>
                <p>
                  Revision {item.value.revision} ·{" "}
                  {String(item.value.state ?? "Explicit scopes")}
                  {item.kind === "connection" &&
                    ` · credential reference ${item.value.secret_ref}`}
                </p>
                <button
                  disabled={held || busy}
                  onClick={() => {
                    setEdit(item);
                    setForwardEdit(null);
                  }}
                >
                  Edit {item.kind} {item.id}
                </button>
                {item.kind === "connection" && (
                  <div>
                    <label>
                      <input
                        type="checkbox"
                        disabled={held || busy}
                        checked={stopped[item.id] ?? false}
                        onChange={(e) =>
                          setStopped({
                            ...stopped,
                            [item.id]: e.target.checked,
                          })
                        }
                      />
                      I have stopped any previous consumer for this connection.
                    </label>
                    <button
                      disabled={
                        held ||
                        busy ||
                        item.value.state !== "active" ||
                        !stopped[item.id]
                      }
                      onClick={() =>
                        void write(
                          `/connections/${encodeURIComponent(item.id)}/activate`,
                          {
                            expected_revision:
                              data.bindings.find(
                                (b) => b.connection_id === item.id,
                              )?.revision ?? 0,
                            old_consumer_stopped: true,
                          },
                        )
                      }
                    >
                      Activate {item.id}
                    </button>
                    {item.value.state !== "active" && (
                      <p>
                        Set this connection active before activation or probing.
                      </p>
                    )}
                    {!data.bindings.some(
                      (binding) => binding.connection_id === item.id,
                    ) && <p>Activate this connection before probing.</p>}
                    {data.health.communications !== "running" && (
                      <p>Probing requires a running communications worker.</p>
                    )}
                    <label>
                      Probe route for {item.id}
                      <select
                        disabled={
                          held ||
                          busy ||
                          item.value.state !== "active" ||
                          data.health.communications !== "running"
                        }
                        value={probeRoutes[item.id] ?? ""}
                        onChange={(e) =>
                          setProbeRoutes({
                            ...probeRoutes,
                            [item.id]: e.target.value,
                          })
                        }
                      >
                        <option value="">Select a route</option>
                        {configs
                          .filter((r) => probeable(r, item.id))
                          .map((r) => (
                            <option key={r.id} value={r.id}>
                              {String(r.value.label || r.id)}
                            </option>
                          ))}
                      </select>
                    </label>
                    <button
                      disabled={
                        held ||
                        busy ||
                        item.value.state !== "active" ||
                        data.health.communications !== "running" ||
                        !data.bindings.some(
                          (binding) => binding.connection_id === item.id,
                        ) ||
                        !configs.some(
                          (route) =>
                            route.id === probeRoutes[item.id] &&
                            probeable(route, item.id),
                        )
                      }
                      onClick={() =>
                        void write(
                          `/connections/${encodeURIComponent(item.id)}/probe`,
                          { route_id: probeRoutes[item.id] },
                        )
                      }
                    >
                      Probe {item.id}
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
          {data.next_after && (
            <button disabled={busy} onClick={() => void load(data.next_after!)}>
              Next configuration page
            </button>
          )}
          <div className="communications-tabs">
            {["connection", "route", "grant"].map((kind) => (
              <button
                key={kind}
                disabled={held || busy}
                onClick={() => {
                  setForwardEdit(null);
                  setEdit({
                    kind,
                    id: "",
                    value: {
                      revision: 0,
                      ...(kind === "connection"
                        ? {
                            transport: "discord",
                            secret_ref: "",
                            bot_id: "",
                            label: "",
                            state: "pending",
                          }
                        : kind === "route"
                          ? {
                              connection_id: "",
                              resident_id: residentId ?? "",
                              address: { guild_id: "", channel_id: "" },
                              label: "",
                              state: "pending",
                              mode: "dedicated",
                              sender_policy: "operators_only",
                              operator_ids: [],
                            }
                          : { read: [], listen: [], reply: [], post: [] }),
                    },
                  });
                }}
              >
                New {kind}
              </button>
            ))}
          </div>
          {edit && (
            <ConfigEditor
              key={`${edit.kind}:${edit.id}:${edit.value.revision}`}
              config={edit}
              disabled={held || busy}
              onSave={(id, value) =>
                void write(
                  `/configuration/${edit.kind}/${encodeURIComponent(id)}`,
                  { expected_revision: edit.value.revision, value },
                  "PUT",
                )
              }
            />
          )}
          <h4>Operator notification forwarding</h4>
          <p>
            Forward only selected new Inbox notices. Enabling establishes a new
            starting point; changing filters does not resend earlier
            notifications.
          </p>
          <ul>
            {forwarding.map((item) => (
              <li key={item.id}>
                {item.id} · revision {item.revision} ·{" "}
                {item.enabled ? "Enabled" : "Disabled"} ·{" "}
                {item.kinds.join(", ") || "No kinds selected"}
                <button
                  disabled={held || busy}
                  onClick={() => {
                    setEdit(null);
                    setForwardEdit(item);
                  }}
                >
                  Edit forwarding {item.id}
                </button>
              </li>
            ))}
          </ul>
          {nextForwarding && (
            <button
              disabled={busy}
              onClick={() => void load("", nextForwarding)}
            >
              Next forwarding page
            </button>
          )}
          <button
            disabled={held || busy}
            onClick={() => {
              setEdit(null);
              setForwardEdit({
                id: "",
                revision: 0,
                destination: {
                  connection_id: "",
                  guild_id: "",
                  channel_id: "",
                },
                kinds: [],
                enabled: false,
                operator_url: null,
                cursor: 0,
                activated_after: 0,
              });
            }}
          >
            New forwarding binding
          </button>
          {forwardEdit && (
            <ForwardEditor
              key={`${forwardEdit.id}:${forwardEdit.revision}`}
              item={forwardEdit}
              disabled={held || busy}
              onSave={(id, body) =>
                void write(`/forwarding/${encodeURIComponent(id)}`, body, "PUT")
              }
            />
          )}
        </>
      )}
    </section>
  );
}
function ScopeEditor({
  value,
  onChange,
  disabled,
  label,
  includeConnection = true,
}: {
  value: Scope;
  onChange: (scope: Scope) => void;
  disabled: boolean;
  label: string;
  includeConnection?: boolean;
}) {
  return (
    <fieldset disabled={disabled}>
      <legend>{label}</legend>
      {(
        [
          ...(includeConnection ? ["connection_id"] : []),
          ...(value.target_id !== undefined
            ? ["target_id"]
            : ["guild_id", "channel_id"]),
        ] as (keyof Scope)[]
      ).map((key) => (
        <label key={key}>
          {words(key)}
          <input
            required
            maxLength={128}
            value={value[key] ?? ""}
            onChange={(e) => onChange({ ...value, [key]: e.target.value })}
          />
        </label>
      ))}
    </fieldset>
  );
}
function ConfigEditor({
  config,
  disabled,
  onSave,
}: {
  config: Config;
  disabled: boolean;
  onSave: (id: string, value: Record<string, unknown>) => void;
}) {
  const [id, setId] = useState(config.id);
  const [value, setValue] = useState<Record<string, unknown>>(() => {
    const { revision, ...rest } = config.value;
    void revision;
    return rest;
  });
  const set = (key: string, v: unknown) =>
    setValue({
      ...value,
      [key]: v,
      ...(key === "transport" && v === "ntfy" ? { bot_id: null } : {}),
    });
  const immutable = (key: string) =>
    !!config.id &&
    (config.kind === "connection"
      ? ["transport", "bot_id"]
      : config.kind === "route"
        ? ["connection_id", "resident_id"]
        : []
    ).includes(key);
  const field = (key: string, label: string, options?: string[]) => (
    <label key={key}>
      {label}
      {options ? (
        <select
          aria-label={label}
          disabled={immutable(key)}
          value={String(value[key] ?? "")}
          onChange={(e) => set(key, e.target.value)}
        >
          {options.map((o) => (
            <option key={o} value={o}>
              {words(o)}
            </option>
          ))}
        </select>
      ) : (
        <input
          required={
            key !== "label" && (key !== "bot_id" || value.transport !== "ntfy")
          }
          disabled={
            immutable(key) || (key === "bot_id" && value.transport === "ntfy")
          }
          maxLength={key === "secret_ref" ? 64 : key === "label" ? 100 : 128}
          value={String(value[key] ?? "")}
          onChange={(e) =>
            set(key, e.target.value || (key === "bot_id" ? null : ""))
          }
        />
      )}
    </label>
  );
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSave(
          id,
          config.kind === "connection" && value.transport === "ntfy"
            ? { ...value, bot_id: null }
            : value,
        );
      }}
      aria-label={`Edit ${config.kind}`}
    >
      <h4>
        {config.id ? "Edit" : "New"} {config.kind}
      </h4>
      <fieldset disabled={disabled}>
        <legend>Revision {config.value.revision}</legend>
        <label>
          {config.kind === "grant" ? "Resident ID" : "Stable local ID"}
          <input
            required
            value={id}
            maxLength={128}
            disabled={!!config.id}
            onChange={(e) => setId(e.target.value)}
          />
        </label>
        {config.kind !== "grant" && (
          <>
            {field("label", "Display label")}
            {field("state", "State", ["pending", "active", "disabled"])}
          </>
        )}
        {config.kind === "connection" && (
          <>
            {!!config.id && (
              <p>
                Transport and external identity are fixed. Create a new
                connection for a different identity.
              </p>
            )}
            {field("transport", "Transport", ["discord", "telegram", "ntfy"])}
            {field(
              "secret_ref",
              "Protected credential reference (never a token)",
            )}
            {field(
              "bot_id",
              "Verified external identity (empty for notification-only transports)",
            )}
          </>
        )}
        {config.kind === "route" && (
          <>
            {!!config.id && (
              <p>
                Source binding is fixed. Create a new route to change
                connection, resident or address.
              </p>
            )}
            {field("connection_id", "Connection ID")}
            {field("resident_id", "Resident ID")}
            <ScopeEditor
              label="Source address"
              value={{
                ...(value.address as Scope),
                connection_id: String(value.connection_id ?? ""),
              }}
              disabled={disabled || !!config.id}
              includeConnection={false}
              onChange={(scope) => {
                const { connection_id, ...address } = scope;
                setValue({ ...value, connection_id, address });
              }}
            />
            {field("mode", "Ownership", ["dedicated", "shared"])}
            {field("sender_policy", "Sender policy", [
              "operators_only",
              "guild_channel_humans",
            ])}
            <label>
              Operator sender IDs (one per line)
              <textarea
                value={(value.operator_ids as string[]).join("\n")}
                onChange={(e) =>
                  set(
                    "operator_ids",
                    e.target.value.split(/\s+/).filter(Boolean),
                  )
                }
              />
            </label>
          </>
        )}
        {config.kind === "grant" &&
          ["read", "listen", "reply", "post"].map((key) => (
            <div key={key}>
              <h5>
                {
                  (
                    {
                      read: "Read history",
                      listen: "Mention triggers",
                      reply: "Reply",
                      post: "Publish announcements",
                    } as Record<string, string>
                  )[key]
                }
              </h5>
              {(value[key] as Scope[]).map((scope, i) => (
                <div key={i}>
                  <ScopeEditor
                    label={`${key} scope ${i + 1}`}
                    value={scope}
                    disabled={disabled}
                    onChange={(next) =>
                      set(
                        key,
                        (value[key] as Scope[]).map((old, index) =>
                          i === index ? next : old,
                        ),
                      )
                    }
                  />
                  <button
                    type="button"
                    onClick={() =>
                      set(
                        key,
                        (value[key] as Scope[]).filter(
                          (_, index) => index !== i,
                        ),
                      )
                    }
                  >
                    Remove {key} scope {i + 1}
                  </button>
                </div>
              ))}
              <button
                type="button"
                disabled={(value[key] as Scope[]).length >= 32}
                onClick={() =>
                  set(key, [
                    ...(value[key] as Scope[]),
                    { connection_id: "", guild_id: "", channel_id: "" },
                  ])
                }
              >
                Add {key} scope
              </button>
            </div>
          ))}
        <button>Save {config.kind}</button>
      </fieldset>
    </form>
  );
}
function ForwardEditor({
  item,
  disabled,
  onSave,
}: {
  item: Forwarding;
  disabled: boolean;
  onSave: (id: string, body: unknown) => void;
}) {
  const [id, setId] = useState(item.id);
  const [destination, setDestination] = useState(item.destination);
  const [kinds, setKinds] = useState(item.kinds);
  const [enabled, setEnabled] = useState(!!item.enabled);
  const [origin, setOrigin] = useState(item.operator_url ?? "");
  return (
    <form
      aria-label="Edit forwarding"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(id, {
          expected_revision: item.revision,
          destination,
          kinds,
          enabled,
          operator_url: origin || null,
        });
      }}
    >
      <fieldset disabled={disabled}>
        <legend>Forwarding revision {item.revision}</legend>
        <label>
          Forwarding ID
          <input
            required
            disabled={!!item.id}
            value={id}
            onChange={(e) => setId(e.target.value)}
          />
        </label>
        <label>
          Destination kind
          <select
            aria-label="Destination kind"
            disabled={!!item.id}
            value={destination.target_id !== undefined ? "target" : "channel"}
            onChange={(e) =>
              setDestination(
                e.target.value === "target"
                  ? { connection_id: destination.connection_id, target_id: "" }
                  : {
                      connection_id: destination.connection_id,
                      guild_id: "",
                      channel_id: "",
                    },
              )
            }
          >
            <option value="channel">Channel</option>
            <option value="target">Notification target slot</option>
          </select>
        </label>
        <ScopeEditor
          label="Immutable destination"
          value={destination}
          disabled={disabled || !!item.id}
          onChange={setDestination}
        />
        {["run.succeeded", "run.failed", "run.cancelled"].map((kind) => (
          <label key={kind}>
            <input
              type="checkbox"
              checked={kinds.includes(kind)}
              onChange={(e) =>
                setKinds(
                  e.target.checked
                    ? [...kinds, kind]
                    : kinds.filter((k) => k !== kind),
                )
              }
            />
            {kind}
          </label>
        ))}
        <label>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Forward new notifications
        </label>
        <label>
          Operator home origin (optional)
          <input
            type="url"
            value={origin}
            placeholder="https://hearth.example"
            onChange={(e) => setOrigin(e.target.value)}
          />
        </label>
        <button>Save forwarding</button>
      </fieldset>
    </form>
  );
}
