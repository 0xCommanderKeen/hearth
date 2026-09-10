import { useEffect, useState, type ReactNode } from "react";
import { Client, type Configuration, type Resident } from "../../shared/client";
import { ResidentPanel, type ResidentTab } from "./Overview";

export function ResidentDetails({
  client,
  resident,
  active,
  onSelect,
  memoryChildren,
  journalChildren,
  skillsChildren,
}: {
  client: Client;
  resident: Resident;
  active: ResidentTab;
  onSelect: (tab: ResidentTab) => void;
  memoryChildren: ReactNode;
  journalChildren: ReactNode;
  skillsChildren: ReactNode;
}) {
  const [configuration, setConfiguration] = useState<Configuration | null>(
    null,
  );
  const [error, setError] = useState(false);
  useEffect(() => {
    let current = true;
    setConfiguration(null);
    setError(false);
    void client.configuration(resident.id).then(
      (value) => {
        if (current) setConfiguration(value);
      },
      () => {
        if (current) setError(true);
      },
    );
    return () => {
      current = false;
    };
  }, [
    client,
    resident.id,
    resident.revision,
    resident.memory_revision,
    resident.input_revision,
  ]);
  const content = (text: string | undefined) =>
    error ? (
      <p role="alert">
        Resident details could not be loaded. Reopen the page to try again.
      </p>
    ) : !configuration ? (
      <p role="status">Loading resident details…</p>
    ) : (
      <pre className="resident-text">{text || "Nothing saved yet."}</pre>
    );
  const grant =
    resident.management && !("error" in resident.management)
      ? resident.management
      : null;
  const heading = (title: string, description: string) => (
    <header className="resident-tab-heading">
      <span className="eyebrow">
        {resident.name} / {active}
      </span>
      <h2>{title}</h2>
      <p>{description}</p>
    </header>
  );
  return (
    <>
      <ResidentPanel name="Memory" active={active}>
        {heading(
          "Memory & journal",
          "What this resident remembers, and the notes its runs have written.",
        )}
        <div className="resident-memory-grid">
          <div>
            <section className="resident-card resident-document">
              <div className="resident-card-head">
                <h3>Current memory</h3>
                <span className="chip">
                  Revision {configuration?.memory.expected_revision ?? "…"}
                </span>
                <button className="quiet" onClick={() => onSelect("Settings")}>
                  Edit in settings →
                </button>
              </div>
              {content(configuration?.memory.text)}
            </section>
            {memoryChildren}
          </div>
          <section className="resident-card resident-journal-card">
            {journalChildren}
          </section>
        </div>
      </ResidentPanel>
      <ResidentPanel name="Skills" active={active}>
        {heading(
          "Skills & instructions",
          "How this resident approaches work, and the skills assigned to it.",
        )}
        <section className="resident-card resident-document">
          <div className="resident-card-head">
            <h3>Instructions</h3>
            <span className="chip">
              Revision {configuration?.declaration.expected_revision ?? "…"}
            </span>
            <button className="quiet" onClick={() => onSelect("Settings")}>
              Edit in settings →
            </button>
          </div>
          {content(configuration?.declaration.instructions)}
        </section>
        <section className="resident-assigned-skills">
          <div className="resident-card-head">
            <h3>Assigned skills</h3>
            <a href="#skills">Skill library →</a>
          </div>
          <p>
            Instructions describe how to work. They do not grant access to tools
            or data.
          </p>
          {resident.skills_error ? (
            <p role="alert">{resident.skills_error.replaceAll("_", " ")}</p>
          ) : !resident.skills?.length ? (
            <p>No assigned skills in the current snapshot.</p>
          ) : (
            <div className="resident-skill-grid">
              {resident.skills.map((skill) => (
                <article
                  className="resident-card resident-skill-card"
                  key={skill.skill_id}
                >
                  <span className="resident-skill-icon" aria-hidden="true">
                    ✧
                  </span>
                  <div>
                    <h3>
                      <a href={`#skills/${encodeURIComponent(skill.skill_id)}`}>
                        {skill.name}
                      </a>
                    </h3>
                    <p>{skill.description || "No description provided."}</p>
                    <small>
                      Assigned revision {skill.revision}
                      {skill.latest_revision &&
                      skill.latest_revision > skill.revision
                        ? ` · Revision ${skill.latest_revision} available`
                        : ""}
                    </small>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
        <details className="resident-technical">
          <summary>Execution profile & provenance</summary>
          {skillsChildren}
        </details>
      </ResidentPanel>
      <ResidentPanel name="Access" active={active}>
        {heading(
          "Access & permissions",
          "The tools and data this resident is allowed to use.",
        )}
        <section className="resident-card resident-document">
          <div className="resident-card-head">
            <h3>Management tools</h3>
            <span className="chip">
              {grant?.enabled ? "Enabled" : "Not enabled"}
            </span>
          </div>
          <p>
            Operator grants determine what this resident can manage.
            Instructions and skills do not grant access.
          </p>
          {resident.management && "error" in resident.management ? (
            <p role="alert">{resident.management.error.replaceAll("_", " ")}</p>
          ) : (
            <dl className="resident-permission-grid">
              <div>
                <dt>Provider authentication</dt>
                <dd>
                  {resident.logins?.[
                    resident.profile?.execution_profile ?? ""
                  ] === "resident"
                    ? "Resident login"
                    : resident.logins?.[
                          resident.profile?.execution_profile ?? ""
                        ] === "household"
                      ? "Household login"
                      : "Not reported"}
                </dd>
              </div>
              <div>
                <dt>Capabilities</dt>
                <dd>
                  {grant?.enabled
                    ? grant.capabilities
                        .map((value) => value.replaceAll("_", " "))
                        .join(", ") || "None"
                    : "No management capabilities enabled"}
                </dd>
              </div>
              <div>
                <dt>Managed residents</dt>
                <dd>
                  {grant?.enabled ? `Up to ${grant.max_residents}` : "None"}
                </dd>
              </div>
              <div>
                <dt>Calls per run</dt>
                <dd>{grant?.enabled ? grant.max_calls : "None"}</dd>
              </div>
              <div>
                <dt>Maximum daily limit granted</dt>
                <dd>
                  {grant?.enabled
                    ? `$${(grant.max_daily_limit / 1e6).toFixed(2)}`
                    : "None"}
                </dd>
              </div>
            </dl>
          )}
          <a href={`#management/${resident.id}`}>
            Review or edit permissions →
          </a>
        </section>
        <section className="resident-card resident-document">
          <div className="resident-card-head">
            <h2>Inputs & folders</h2>
            <button className="quiet" onClick={() => onSelect("Settings")}>
              Edit inputs in settings →
            </button>
          </div>
          {resident.inputs_error ? (
            <p role="alert">{resident.inputs_error.replaceAll("_", " ")}</p>
          ) : (
            <>
              <h3>Selected input sets</h3>
              {resident.input_sets?.length ? (
                <ul>
                  {resident.input_sets.map((input) => (
                    <li key={input.input_set_id}>{input.name}</li>
                  ))}
                </ul>
              ) : (
                <p>No input sets listed.</p>
              )}
            </>
          )}
          {resident.management && !("error" in resident.management) && (
            <>
              <h3>Granted folders</h3>
              {resident.management.mounts?.length ? (
                <ul>
                  {resident.management.mounts.map((mount) => (
                    <li key={mount.name}>
                      {mount.name} ·{" "}
                      {mount.mode === "rw" ? "Read and write" : "Read only"}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>No mounted folders granted.</p>
              )}
            </>
          )}
          <a href="#inputs">Open input library →</a>
        </section>
      </ResidentPanel>
    </>
  );
}
