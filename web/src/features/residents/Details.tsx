import { useEffect, useState, type ReactNode } from "react";
import { Client, type Configuration, type Resident } from "../../shared/client";
import { ResidentPanel, type ResidentTab } from "./Overview";

export function ResidentDetails({
  client,
  resident,
  active,
  onSelect,
  memoryChildren,
  skillsChildren,
  accessChildren,
}: {
  client: Client;
  resident: Resident;
  active: ResidentTab;
  onSelect: (tab: ResidentTab) => void;
  memoryChildren: ReactNode;
  skillsChildren: ReactNode;
  accessChildren: ReactNode;
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
  return (
    <>
      <ResidentPanel name="Memory" active={active}>
        <section className="resident-card resident-document">
          <div className="resident-card-head">
            <h2>Current memory</h2>
            <button className="quiet" onClick={() => onSelect("Settings")}>
              Edit in settings →
            </button>
          </div>
          {content(configuration?.memory.text)}
        </section>
        {memoryChildren}
      </ResidentPanel>
      <ResidentPanel name="Skills" active={active}>
        <section className="resident-card resident-document">
          <div className="resident-card-head">
            <h2>Instructions</h2>
            <button className="quiet" onClick={() => onSelect("Settings")}>
              Edit in settings →
            </button>
          </div>
          {content(configuration?.declaration.instructions)}
        </section>
        <section className="resident-card resident-document">
          <div className="resident-card-head">
            <h2>Assigned skills</h2>
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
            <ul>
              {resident.skills.map((skill) => (
                <li key={skill.skill_id}>
                  <a href={`#skills/${encodeURIComponent(skill.skill_id)}`}>
                    {skill.name}
                  </a>{" "}
                  · revision {skill.revision}
                </li>
              ))}
            </ul>
          )}
        </section>
        {skillsChildren}
      </ResidentPanel>
      <ResidentPanel name="Access" active={active}>
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
        {accessChildren}
      </ResidentPanel>
    </>
  );
}
