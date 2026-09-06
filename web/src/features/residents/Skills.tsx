import { Client } from "../../shared/client";
import { TextEditor, type EditorControls } from "../../shared/TextEditor";

export function Skills({
  client,
  ...controls
}: EditorControls & { client: Client }) {
  return (
    <TextEditor
      {...controls}
      kind="resident instructions"
      revision={controls.resident.revision}
      load={() => client.resident(controls.resident.id)}
      save={(saved, draft) => client.saveResident(saved, draft)}
      text={(saved) => saved.declaration.skill_text}
      limit={32000}
      description="Resident-specific instructions, distinct from the reusable skills above. They do not grant access to files, tools or external actions."
      hint={(revision) =>
        `Editing revision ${revision}. Changes apply to newly admitted runs. Existing work keeps its pinned revision.`
      }
    />
  );
}
