import { useEffect, useState } from "react";
import { Client, type SkillUser } from "../../shared/client";
export function SkillUsers({ client, id }: { client: Client; id: string }) {
  const [users, setUsers] = useState<SkillUser[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    client
      .skillUsers(id)
      .then((rows) => {
        if (live) setUsers(rows);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [client, id]);
  return (
    <section aria-label="Residents using this skill" className="skill-history">
      <h3>Assigned residents</h3>
      {error ? (
        <p role="alert">{error}</p>
      ) : users === null ? (
        <p>Loading assigned residents…</p>
      ) : !users.length ? (
        <p>No residents use this skill yet.</p>
      ) : (
        <ul>
          {users.map((user) => (
            <li key={user.resident_id}>
              <a href={`#residents/${encodeURIComponent(user.resident_id)}`}>
                {user.name}
              </a>{" "}
              · revision {user.revision} · position {user.position + 1}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
