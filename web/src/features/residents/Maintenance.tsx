import { useEffect, useRef, useState } from "react";
import {
  Client,
  RequestError,
  type CatalogSkill,
  type Configuration,
  type ConfigurationChange,
  type MaintenanceChange,
  type Resident,
  type ResidentOptions,
  type Routine,
} from "../../shared/client";
import "./provisioning.css";

export function ResidentMaintenance({
  client,
  resident,
  readOnly,
  routines,
  onChanged,
}: {
  client: Client;
  resident: Resident;
  readOnly: boolean;
  routines: Routine[];
  onChanged: () => void;
}) {
  const [base, setBase] = useState<Configuration | null>(null);
  const [draft, setDraft] = useState<Configuration | null>(null);
  const [options, setOptions] = useState<ResidentOptions | null>(null);
  const [catalog, setCatalog] = useState<CatalogSkill[]>([]);
  const [editing, setEditing] = useState(false);
  const [manager, setManager] = useState("");
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const [pending, setPending] = useState<MaintenanceChange | null>(null);
  const [busy, setBusy] = useState(false);
  const live = useRef(true);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    live.current = true;
    let active = true;
    void Promise.all([
      client.configuration(resident.id),
      client.request<ResidentOptions>("/api/resident-options"),
      client.skills("", true),
    ])
      .then(([configuration, choices, skills]) => {
        if (!active) return;
        setBase(configuration);
        setDraft(structuredClone(configuration));
        setOptions(choices);
        setCatalog(skills);
        setManager(configuration.lifecycle.manager ?? "operator");
        setConflict(false);
      })
      .catch((e) => {
        if (active)
          setMessage(
            e instanceof Error ? e.message : "Configuration unavailable",
          );
      });
    return () => {
      active = false;
      live.current = false;
    };
  }, [client, resident.id, reload]);
  const lifecycle =
    (resident.lifecycle?.revision ?? -1) > (base?.lifecycle.revision ?? -1)
      ? resident.lifecycle!
      : (base?.lifecycle ?? resident.lifecycle);
  const archived = lifecycle?.state === "archived";
  const locked = readOnly || busy || !!pending || conflict;
  async function submit(change: MaintenanceChange) {
    setPending(change);
    setBusy(true);
    setMessage("");
    try {
      await client.maintainResident(change);
      if (!live.current) return;
      setPending(null);
      setEditing(false);
      setMessage("Resident change saved.");
      setReload((n) => n + 1);
      onChanged();
    } catch (e) {
      if (!live.current) return;
      if (
        e instanceof RequestError &&
        [401, 404, 409, 413, 422].includes(e.status)
      ) {
        setPending(null);
        setConflict(e.status === 409);
        setMessage(
          e.status === 409
            ? "The resident changed. Your draft is retained. Reload current configuration before editing again."
            : e.message,
        );
      } else
        setMessage(
          "Change unconfirmed. Retry the same command to recover its receipt.",
        );
    } finally {
      if (live.current) setBusy(false);
    }
  }
  function changeState(state: "ready" | "paused" | "archived") {
    if (lifecycle?.revision === undefined) return;
    void submit({
      resident_id: resident.id,
      command_id: crypto.randomUUID(),
      kind: "lifecycle",
      body: { expected_revision: lifecycle.revision, state },
    });
  }
  function save() {
    if (!base || !draft || base.lifecycle.revision === undefined) return;
    const body: ConfigurationChange = {
      expected_lifecycle_revision: base.lifecycle.revision,
    };
    for (const key of ["declaration", "memory", "inputs", "skills"] as const) {
      if (JSON.stringify(base[key]) !== JSON.stringify(draft[key]))
        Object.assign(body, { [key]: draft[key] });
    }
    const changed = draft.routines.filter(
      (r) =>
        JSON.stringify(r) !==
        JSON.stringify(
          base.routines.find((b) => b.routine_id === r.routine_id),
        ),
    );
    if (changed.length) body.routines = changed;
    if (Object.keys(body).length === 1) {
      setMessage("No configuration changes to save.");
      return;
    }
    void submit({
      resident_id: resident.id,
      command_id: crypto.randomUUID(),
      kind: "configuration",
      body,
    });
  }
  function declaration(
    key: keyof Configuration["declaration"],
    value: string | number,
  ) {
    setDraft(
      (d) => d && { ...d, declaration: { ...d.declaration, [key]: value } },
    );
  }
  return (
    <section
      className="provisioning maintenance"
      aria-label="Resident configuration"
    >
      <div className="section-title">
        <span className="eyebrow">RESIDENT / CONFIGURATION</span>
        <h3>Care & continuity</h3>
      </div>
      <p>
        <strong>{lifecycle?.state ?? "Unavailable"}</strong> · Lifecycle
        revision {lifecycle?.revision ?? "unavailable"} · Current manager:{" "}
        {lifecycle?.manager ?? "unavailable"}
      </p>
      <p>
        Pausing suspends new runs and routine occurrences. Already admitted work
        continues. Archiving also prevents pending work from launching and
        removes the resident from the active Hamlet. Archive is permanent.
      </p>
      {!!resident.unresolved_runs && (
        <p role="status">
          {resident.unresolved_runs} unresolved run(s) retain their accounting
          holds. Inspect tasks below and cancel explicitly when needed.
        </p>
      )}
      {resident.safety_hold_reason && (
        <p>
          Safety hold: {resident.safety_hold_reason.replaceAll("_", " ")}.
          Resuming does not clear this hold.
        </p>
      )}
      <div className="maintenance-actions">
        {!archived && (
          <>
            <button
              disabled={locked || editing || lifecycle?.revision === undefined}
              onClick={() =>
                changeState(lifecycle?.state === "paused" ? "ready" : "paused")
              }
            >
              {lifecycle?.state === "paused"
                ? "Resume new runs"
                : "Pause new runs"}
            </button>
            <button
              disabled={locked || editing || lifecycle?.revision === undefined}
              onClick={() => changeState("archived")}
            >
              Archive resident
            </button>
          </>
        )}
        {base && !archived && !editing && (
          <button disabled={locked} onClick={() => setEditing(true)}>
            Edit configuration
          </button>
        )}
      </div>
      {message && <p role="status">{message}</p>}
      {pending && (
        <button
          disabled={busy || readOnly}
          onClick={() => void submit(pending)}
        >
          Retry pending resident change
        </button>
      )}
      {conflict && (
        <button
          disabled={busy || !!pending}
          onClick={() => {
            setMessage("");
            setReload((n) => n + 1);
          }}
        >
          Reload current configuration (replace draft)
        </button>
      )}
      {base && (
        <>
          <p>
            Execution profile: {base.execution_profile}. Changes apply to future
            work; run context already recorded stays intact.
          </p>
          <label>
            Manager
            <select
              value={manager}
              disabled={locked}
              onChange={(e) => setManager(e.target.value)}
            >
              {options?.managers.map((m) => (
                <option value={m.id} key={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={
              locked ||
              editing ||
              manager === lifecycle?.manager ||
              lifecycle?.revision === undefined
            }
            onClick={() =>
              void submit({
                resident_id: resident.id,
                command_id: crypto.randomUUID(),
                kind: "manager",
                body: { manager, expected_revision: lifecycle!.revision! },
              })
            }
          >
            Transfer management
          </button>
          <p>
            Transfer changes ownership only. Management grants remain separately
            controlled by the operator.
          </p>
          <p>
            Inputs, in reading order:{" "}
            {base.inputs.input_sets
              .map(
                (i) =>
                  options?.input_sets.find(
                    (o) => o.input_set_id === i.input_set_id,
                  )?.name ?? i.input_set_id,
              )
              .join(" → ") || "None"}
          </p>
          <p>
            Skills:{" "}
            {base.skills.skills
              .map(
                (s) =>
                  `${catalog.find((c) => c.skill_id === s.skill_id)?.name ?? s.skill_id} · revision ${s.revision}`,
              )
              .join(", ") || "None"}
          </p>
          {routines
            .filter((r) => r.resident_id === resident.id)
            .map((r) => (
              <p key={r.id}>
                Daily at {r.local_time} ({r.timezone}) ·{" "}
                {lifecycle?.state !== "ready"
                  ? `Suspended by ${lifecycle?.state} lifecycle`
                  : r.enabled
                    ? `Next: ${new Date(r.next_at * 1000).toLocaleString()}`
                    : "Disabled"}
              </p>
            ))}
        </>
      )}
      {editing && draft && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <fieldset disabled={locked || archived}>
            <h3>Declaration & budget</h3>
            <div className="provision-fields">
              <label>
                Name
                <input
                  value={draft.declaration.name}
                  onChange={(e) => declaration("name", e.target.value)}
                  required
                />
              </label>
              <label>
                Purpose
                <textarea
                  value={draft.declaration.purpose}
                  onChange={(e) => declaration("purpose", e.target.value)}
                  required
                />
              </label>
              <label>
                Daily limit (microdollars)
                <input
                  type="number"
                  min="1"
                  value={draft.declaration.daily_limit}
                  onChange={(e) =>
                    declaration("daily_limit", Number(e.target.value))
                  }
                  required
                />
              </label>
              <label>
                Budget timezone
                <input
                  value={draft.declaration.budget_timezone}
                  onChange={(e) =>
                    declaration("budget_timezone", e.target.value)
                  }
                  required
                />
              </label>
            </div>
            <label>
              Instructions
              <textarea
                value={draft.declaration.instructions}
                onChange={(e) => declaration("instructions", e.target.value)}
              />
            </label>
            <label>
              Memory
              <textarea
                value={draft.memory.text}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    memory: { ...draft.memory, text: e.target.value },
                  })
                }
              />
            </label>
            <h3>Synthetic inputs</h3>
            <p>
              Selected in checkbox order; remove and reselect to change reading
              order. Up to four sets.
            </p>
            {options?.input_sets.map((i) => (
              <label className="provision-check" key={i.input_set_id}>
                <input
                  type="checkbox"
                  checked={draft.inputs.input_sets.some(
                    (s) => s.input_set_id === i.input_set_id,
                  )}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      inputs: {
                        ...draft.inputs,
                        input_sets: e.target.checked
                          ? [
                              ...draft.inputs.input_sets,
                              { input_set_id: i.input_set_id },
                            ]
                          : draft.inputs.input_sets.filter(
                              (s) => s.input_set_id !== i.input_set_id,
                            ),
                      },
                    })
                  }
                />
                {i.name}
              </label>
            ))}
            <h3>Skill revisions</h3>
            <p>
              Each assignment pins an exact revision. Existing revisions are
              never upgraded automatically.
            </p>
            {draft.skills.skills.map((s, index) => (
              <div className="maintenance-skill" key={s.skill_id}>
                <a href={`#skills/${s.skill_id}`}>
                  {catalog.find((c) => c.skill_id === s.skill_id)?.name ??
                    s.skill_id}
                </a>
                <label>
                  Revision for {s.skill_id}
                  <input
                    type="number"
                    min="1"
                    value={s.revision}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        skills: {
                          ...draft.skills,
                          skills: draft.skills.skills.map((item, n) =>
                            n === index
                              ? { ...item, revision: Number(e.target.value) }
                              : item,
                          ),
                        },
                      })
                    }
                  />
                </label>
                <button
                  type="button"
                  disabled={index === 0}
                  onClick={() => {
                    const skills = [...draft.skills.skills];
                    [skills[index - 1], skills[index]] = [
                      skills[index],
                      skills[index - 1],
                    ];
                    setDraft({ ...draft, skills: { ...draft.skills, skills } });
                  }}
                >
                  Move up
                </button>
                <button
                  type="button"
                  onClick={() =>
                    setDraft({
                      ...draft,
                      skills: {
                        ...draft.skills,
                        skills: draft.skills.skills.filter(
                          (_, n) => n !== index,
                        ),
                      },
                    })
                  }
                >
                  Remove skill
                </button>
              </div>
            ))}
            <label>
              Add skill
              <select
                value=""
                disabled={draft.skills.skills.length >= 8}
                onChange={(e) => {
                  const skill = catalog.find(
                    (s) => s.skill_id === e.target.value,
                  );
                  if (skill)
                    setDraft({
                      ...draft,
                      skills: {
                        ...draft.skills,
                        skills: [
                          ...draft.skills.skills,
                          {
                            skill_id: skill.skill_id,
                            revision: skill.revision,
                          },
                        ],
                      },
                    });
                }}
              >
                <option value="">Choose an exact current revision</option>
                {catalog
                  .filter(
                    (c) =>
                      c.status === "active" &&
                      !draft.skills.skills.some(
                        (s) => s.skill_id === c.skill_id,
                      ),
                  )
                  .map((c) => (
                    <option key={c.skill_id} value={c.skill_id}>
                      {c.name} · revision {c.revision}
                    </option>
                  ))}
              </select>
            </label>
            <h3>Daily routines</h3>
            {draft.routines.map((r, index) => (
              <fieldset className="maintenance-routine" key={r.routine_id}>
                <legend>
                  Routine {index + 1} · revision {r.expected_revision}
                </legend>
                {(["instruction", "local_time", "timezone"] as const).map(
                  (key) => (
                    <label key={key}>
                      {key === "local_time"
                        ? "Daily time"
                        : key === "instruction"
                          ? "Routine instruction"
                          : "Routine timezone"}
                      <input
                        type={key === "local_time" ? "time" : "text"}
                        value={r[key]}
                        required
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            routines: draft.routines.map((item, n) =>
                              n === index
                                ? { ...item, [key]: e.target.value }
                                : item,
                            ),
                          })
                        }
                      />
                    </label>
                  ),
                )}
                <label className="provision-check">
                  <input
                    type="checkbox"
                    checked={r.enabled}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        routines: draft.routines.map((item, n) =>
                          n === index
                            ? { ...item, enabled: e.target.checked }
                            : item,
                        ),
                      })
                    }
                  />
                  Enabled when resident is ready
                </label>
              </fieldset>
            ))}
            <button
              type="button"
              onClick={() =>
                setDraft({
                  ...draft,
                  routines: [
                    ...draft.routines,
                    {
                      routine_id: crypto.randomUUID(),
                      expected_revision: 0,
                      instruction: "Summarize today’s synthetic notes.",
                      local_time: "09:00",
                      timezone: draft.declaration.budget_timezone,
                      enabled: false,
                    },
                  ],
                })
              }
            >
              Add daily routine
            </button>
            <div className="maintenance-actions">
              <button className="primary">Save configuration</button>
              <button
                type="button"
                onClick={() => {
                  setDraft(structuredClone(base));
                  setEditing(false);
                }}
              >
                Discard draft
              </button>
            </div>
          </fieldset>
        </form>
      )}
    </section>
  );
}
