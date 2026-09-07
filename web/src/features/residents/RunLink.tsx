/**
 * A run opens only while its task is still on the page. The anchor `#run-<id>` targets
 * a row in Tasks & results, and that list holds the newest hundred tasks, so an entry
 * or revision older than the window has nothing to scroll to. Say so rather than
 * offering a link that does nothing.
 */
export function RunLink({
  runId,
  openable,
  label,
}: {
  runId: string;
  openable: boolean;
  label: string;
}) {
  if (!openable)
    return (
      <small>Run {runId} · no longer in this resident's recent work</small>
    );
  return <a href={`#run-${encodeURIComponent(runId)}`}>{label}</a>;
}
