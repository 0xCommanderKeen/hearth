import { Client } from "../../shared/client";
import { TextEditor, type EditorControls } from "../../shared/TextEditor";

export function Memory({
  client,
  ...controls
}: EditorControls & { client: Client }) {
  return (
    <TextEditor
      {...controls}
      kind="memory"
      revision={controls.resident.memory_revision ?? 0}
      load={() => client.memory(controls.resident.id)}
      save={(saved, draft) =>
        client.saveMemory(controls.resident.id, draft, saved.revision)
      }
      text={(saved) => saved.text}
      limit={131072}
      description="Notes carried from one task to the next. You can edit this Markdown; Reader can only read the version pinned to its run."
      hint={(revision) =>
        `Memory revision ${revision}. Up to 128 KiB of UTF-8 text. Existing runs keep their original memory; new runs use the latest saved revision.`
      }
    />
  );
}
