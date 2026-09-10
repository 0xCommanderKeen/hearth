import { useEffect, useRef, useState } from "react";
import { type Client, type Task } from "../../shared/client";

/** Full task content is immutable and loaded only when the operator opens it. */
export function TaskInstruction({
  client,
  task,
}: {
  client: Client;
  task: Task;
}) {
  const [full, setFull] = useState<string>();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    setFull(undefined);
    setOpen(false);
    setLoading(false);
    setError("");
    return () => {
      generation.current += 1;
    };
  }, [client, task.id]);
  const expand = async () => {
    const requestGeneration = generation.current;
    if (open) {
      setOpen(false);
      return;
    }
    if (full !== undefined) {
      setOpen(true);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const detail = await client.task(task.id);
      if (generation.current !== requestGeneration) return;
      setFull(detail.instruction);
      setOpen(true);
    } catch (error) {
      if (generation.current !== requestGeneration) return;
      setError(
        error instanceof Error ? error.message : "Could not load instructions.",
      );
    } finally {
      if (generation.current === requestGeneration) setLoading(false);
    }
  };
  return (
    <>
      <h3>
        {task.instruction}
        {task.instruction_truncated && "…"}
      </h3>
      {task.instruction_truncated && (
        <button
          type="button"
          disabled={loading}
          aria-expanded={open}
          onClick={expand}
        >
          {loading
            ? "Loading instructions…"
            : open
              ? "Hide full instructions"
              : "Read full instructions"}
        </button>
      )}
      {error && <p role="alert">{error}</p>}
      {open && (
        <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
          {full}
        </p>
      )}
    </>
  );
}
