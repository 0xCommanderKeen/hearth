import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Client,
  RequestError,
  type CatalogSkill,
  type ProvisionRequest,
  type ProvisionReceipt,
  type ResidentOptions,
  type ResidentProfile,
} from "../../shared/client";
import "./provisioning.css";

const empty: ProvisionRequest = {
  name: "",
  purpose: "",
  instructions: "",
  initial_memory: "",
  skills: [],
  execution_profile: "",
  input_sets: [],
  daily_limit: 100000,
  budget_timezone: "Europe/Ljubljana",
  creation_reason: "",
  manager: "operator",
  routine: null,
  first_assignment: null,
};
export function NewResident({
  client,
  readOnly,
  commandId,
  onCreated,
}: {
  client: Client;
  readOnly: boolean;
  commandId: string;
  onCreated: (receipt: ProvisionReceipt) => Promise<void>;
}) {
  const [draft, setDraft] = useState<ProvisionRequest>(empty);
  const [options, setOptions] = useState<ResidentOptions | null>(null);
  const [skills, setSkills] = useState<CatalogSkill[]>([]);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState<ProvisionReceipt | null>(null);
  const pending = useRef<{ id: string; body: ProvisionRequest } | null>(null);
  const [uncertain, setUncertain] = useState(false);
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
      const [choices, catalog, previous] = await Promise.all([
        client.request<ResidentOptions>("/api/resident-options"),
        client.skills(),
        commandId
          ? client.request<ProvisionReceipt>(
              `/api/resident-provisioning/${encodeURIComponent(commandId)}`,
            )
          : Promise.resolve(null),
      ]);
      if (!live.current) return;
      setOptions(choices);
      setSkills(catalog);
      if (previous) {
        setDraft(previous.setup);
        setReceipt(previous);
        pending.current = { id: previous.command_id, body: previous.setup };
      } else
        setDraft({
          ...empty,
          execution_profile: choices.execution_profiles[0]?.id ?? "",
        });
    } catch (e) {
      if (live.current)
        setError(
          e instanceof Error ? e.message : "Could not load setup choices.",
        );
    } finally {
      if (live.current) setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, [client, commandId]);
  async function save(event?: FormEvent) {
    event?.preventDefault();
    if (busy || readOnly) return;
    pending.current ??= {
      id: crypto.randomUUID(),
      body: structuredClone(draft),
    };
    setBusy(true);
    setError("");
    let confirmed: ProvisionReceipt | null = null;
    try {
      const result = await client.provision(
        pending.current.id,
        pending.current.body,
      );
      if (!live.current) return;
      confirmed = result;
      setReceipt(result);
      setUncertain(false);
      if (result.status === "ready") await onCreated(result);
    } catch (e) {
      if (!live.current) return;
      if (confirmed?.status === "ready") {
        setUncertain(false);
        setError(
          "Resident is ready. Refreshing the view failed; open the saved resident link below.",
        );
        return;
      }
      if (
        e instanceof RequestError &&
        [401, 409, 413, 422].includes(e.status)
      ) {
        pending.current = null;
        setError(e.message);
        setUncertain(false);
      } else {
        setUncertain(true);
        setError(
          "Setup is unconfirmed. Retry the same operation to recover its result; your exact setup is retained.",
        );
      }
    } finally {
      if (live.current) setBusy(false);
    }
  }
  const locked = busy || readOnly || uncertain || receipt !== null;
  const update = <K extends keyof ProvisionRequest>(
    key: K,
    value: ProvisionRequest[K],
  ) => setDraft({ ...draft, [key]: value });
  return (
    <section className="provisioning" aria-label="New resident setup">
      <span className="eyebrow">A PLACE IN HEARTH</span>
      <h2>Define useful work.</h2>
      <p>
        Purpose, memory and skills become one complete resident. Its first
        assignment stays queued until you start it.
      </p>
      {error && (
        <p role="alert" className="notice error">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">Loading configured choices…</p>
      ) : !options ? (
        <button onClick={() => void load()}>Retry loading setup</button>
      ) : (
        <>
          {receipt && (
            <div className="notice" role="status">
              <strong>
                {receipt.status === "ready"
                  ? "Ready for work"
                  : receipt.status === "failed"
                    ? "Setup failed"
                    : "Setting up"}
              </strong>
              <p>{receipt.reason?.replaceAll("_", " ")}</p>
              <small>
                Resident {receipt.resident_id} · operation {receipt.command_id}
              </small>
              {receipt.status === "ready" && (
                <p>
                  <a href={`#residents/${receipt.resident_id}`}>
                    Open resident →
                  </a>
                </p>
              )}
            </div>
          )}
          <form onSubmit={(e) => void save(e)}>
            <fieldset disabled={locked}>
              <div className="provision-fields">
                <label>
                  Resident name
                  <input
                    required
                    maxLength={100}
                    value={draft.name}
                    onChange={(e) => update("name", e.target.value)}
                  />
                </label>
                <label>
                  Manager
                  <select
                    value={draft.manager}
                    onChange={(e) => update("manager", e.target.value)}
                  >
                    {options.managers.map((manager) => (
                      <option key={manager.id} value={manager.id}>
                        {manager.name}
                        {manager.id === "operator" ? "" : ` · ${manager.id}`}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <label>
                Purpose
                <textarea
                  required
                  maxLength={8000}
                  value={draft.purpose}
                  onChange={(e) => update("purpose", e.target.value)}
                  placeholder="What useful outcome should this resident produce?"
                />
              </label>
              <label>
                Creation reason
                <input
                  required
                  maxLength={2000}
                  value={draft.creation_reason}
                  onChange={(e) => update("creation_reason", e.target.value)}
                />
              </label>
              <div className="provision-fields">
                <label>
                  Resident instructions
                  <textarea
                    maxLength={32000}
                    value={draft.instructions}
                    onChange={(e) => update("instructions", e.target.value)}
                  />
                </label>
                <label>
                  Initial memory
                  <textarea
                    maxLength={131072}
                    value={draft.initial_memory}
                    onChange={(e) => update("initial_memory", e.target.value)}
                  />
                </label>
              </div>
              <h3>Skills to use, in order</h3>
              <p>
                Exact revisions are pinned. Skill text cannot grant permissions.
              </p>
              <ol>
                {draft.skills.map((entry, index) => (
                  <li key={entry.skill_id}>
                    {skills.find((skill) => skill.skill_id === entry.skill_id)
                      ?.name ?? entry.skill_id}{" "}
                    · revision {entry.revision}{" "}
                    <button
                      type="button"
                      disabled={locked || index === 0}
                      aria-label={`Move setup skill ${index + 1} up`}
                      onClick={() => {
                        const next = [...draft.skills];
                        [next[index - 1], next[index]] = [
                          next[index],
                          next[index - 1],
                        ];
                        update("skills", next);
                      }}
                    >
                      ↑
                    </button>{" "}
                    <button
                      type="button"
                      onClick={() =>
                        update(
                          "skills",
                          draft.skills.filter((_, i) => i !== index),
                        )
                      }
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ol>
              <label>
                Add a shared skill
                <select
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                >
                  <option value="">Choose a skill…</option>
                  {skills
                    .filter(
                      (skill) =>
                        !draft.skills.some(
                          (entry) => entry.skill_id === skill.skill_id,
                        ),
                    )
                    .map((skill) => (
                      <option key={skill.skill_id} value={skill.skill_id}>
                        {skill.name} · revision {skill.revision}
                      </option>
                    ))}
                </select>
              </label>
              <button
                type="button"
                disabled={locked || !selected || draft.skills.length >= 8}
                onClick={() => {
                  const skill = skills.find((row) => row.skill_id === selected);
                  if (skill)
                    update("skills", [
                      ...draft.skills,
                      { skill_id: skill.skill_id, revision: skill.revision },
                    ]);
                  setSelected("");
                }}
              >
                Add selected skill
              </button>
              <h3>Execution and inputs</h3>
              <label>
                Execution profile
                <select
                  required
                  value={draft.execution_profile}
                  onChange={(e) => update("execution_profile", e.target.value)}
                >
                  {options.execution_profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </label>
              <p>
                Uses this installation’s configured profile. Residents do not
                need separate subscription logins.
              </p>
              {options.input_sets.map((input) => (
                <label className="provision-check" key={input.input_set_id}>
                  <input
                    type="checkbox"
                    checked={draft.input_sets.some(
                      (ref) => ref.input_set_id === input.input_set_id,
                    )}
                    onChange={(e) =>
                      update(
                        "input_sets",
                        e.target.checked
                          ? [{ input_set_id: input.input_set_id }]
                          : [],
                      )
                    }
                  />
                  {input.name}
                </label>
              ))}
              {!draft.input_sets.length && (
                <p>
                  No inputs selected. This resident receives no synthetic notes.
                </p>
              )}
              <div className="provision-fields">
                <label>
                  Resident daily allowance ($)
                  <input
                    type="number"
                    min="0"
                    step="0.000001"
                    required
                    value={draft.daily_limit / 1000000}
                    onChange={(e) =>
                      update(
                        "daily_limit",
                        Math.round(Number(e.target.value) * 1000000),
                      )
                    }
                  />
                </label>
                <label>
                  Resident budget timezone
                  <input
                    required
                    value={draft.budget_timezone}
                    onChange={(e) => update("budget_timezone", e.target.value)}
                  />
                </label>
              </div>
              <p>
                Every run also respects the shared household allowance and
                concurrency limit.
              </p>
              <h3>First work</h3>
              <label className="provision-check">
                <input
                  type="checkbox"
                  checked={draft.first_assignment !== null}
                  onChange={(e) =>
                    update(
                      "first_assignment",
                      e.target.checked
                        ? {
                            instruction:
                              "Summarize the selected synthetic notes.",
                          }
                        : null,
                    )
                  }
                />
                Queue a first assignment
              </label>
              {draft.first_assignment && (
                <label>
                  First assignment
                  <textarea
                    required
                    value={draft.first_assignment.instruction}
                    onChange={(e) =>
                      update("first_assignment", {
                        instruction: e.target.value,
                      })
                    }
                  />
                </label>
              )}
              <label className="provision-check">
                <input
                  type="checkbox"
                  checked={draft.routine !== null}
                  onChange={(e) =>
                    update(
                      "routine",
                      e.target.checked
                        ? {
                            instruction:
                              "Summarize the selected synthetic notes.",
                            local_time: "09:00",
                            timezone: "Europe/Ljubljana",
                            enabled: true,
                          }
                        : null,
                    )
                  }
                />
                Add a daily routine
              </label>
              {draft.routine && (
                <>
                  <label>
                    Routine instruction
                    <textarea
                      required
                      value={draft.routine.instruction}
                      onChange={(e) =>
                        update("routine", {
                          ...draft.routine!,
                          instruction: e.target.value,
                        })
                      }
                    />
                  </label>
                  <div className="provision-fields">
                    <label>
                      Routine time
                      <input
                        type="time"
                        required
                        value={draft.routine.local_time}
                        onChange={(e) =>
                          update("routine", {
                            ...draft.routine!,
                            local_time: e.target.value,
                          })
                        }
                      />
                    </label>
                    <label>
                      Routine timezone
                      <input
                        required
                        value={draft.routine.timezone}
                        onChange={(e) =>
                          update("routine", {
                            ...draft.routine!,
                            timezone: e.target.value,
                          })
                        }
                      />
                    </label>
                  </div>
                </>
              )}
            </fieldset>
            <button className="primary" disabled={locked}>
              {busy ? "Setting up…" : "Create resident"}
            </button>
          </form>
          {(uncertain || receipt?.status === "failed") && (
            <button disabled={busy || readOnly} onClick={() => void save()}>
              Retry same setup
            </button>
          )}
          {receipt?.status === "failed" && (
            <button
              disabled={busy || readOnly}
              onClick={() => {
                pending.current = null;
                setReceipt(null);
                setError("");
              }}
            >
              Edit as a new setup
            </button>
          )}
        </>
      )}
    </section>
  );
}
export function ProfileProvenance({ profile }: { profile: ResidentProfile }) {
  return (
    <section className="provision-profile" aria-label="Resident setup profile">
      <h3>Ready for work</h3>
      <dl className="provision-fields">
        <div>
          <dt>Created by</dt>
          <dd>
            {profile.creator_name ?? profile.creator} ({profile.creator}) ·{" "}
            {new Date(profile.created_at * 1000).toLocaleString()}
          </dd>
        </div>
        <div>
          <dt>Manager</dt>
          <dd>
            {profile.manager_name ?? profile.manager} ({profile.manager})
          </dd>
        </div>
        <div>
          <dt>Execution profile</dt>
          <dd>{profile.execution_profile}</dd>
        </div>
        <div>
          <dt>Inputs</dt>
          <dd>
            {profile.input_sets.length
              ? profile.input_sets.map((ref) => ref.input_set_id).join(", ")
              : "No inputs selected"}
          </dd>
        </div>
      </dl>
      <p>{profile.creation_reason}</p>
      <small>
        Setup operation {profile.command_id}
        {profile.originating_run_id
          ? ` · originating run ${profile.originating_run_id}`
          : ""}
      </small>
    </section>
  );
}
