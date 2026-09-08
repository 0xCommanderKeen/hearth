import type { LetterHop } from "../../shared/client";
import { LetterStateChip } from "./Letters";

/** The chain one letter belongs to, root first, ending at the task being read.
 *
 * Only a letter has a chain: the task a resident was working when it wrote one is the
 * root, and every hop after it names who wrote that hop and what it came to. The whole
 * breadcrumb is read from Hearth's stored lineage, never from anything a resident said
 * about its own parent, so it cannot claim a hop that did not happen.
 */
export function Lineage({ hops }: { hops: LetterHop[] }) {
  if (!hops.length) return null;
  return (
    <nav
      className="lineage"
      aria-label={`Lineage · ${hops.map((hop) => hop.resident_name).join(" → ")}`}
    >
      <ol>
        {hops.map((hop, index) => (
          <li
            key={hop.task_id}
            aria-current={index === hops.length - 1 ? "step" : undefined}
          >
            {index > 0 && (
              <span className="lineage-arrow" aria-hidden="true">
                →
              </span>
            )}
            <a href={`#residents/${encodeURIComponent(hop.resident_id)}`}>
              {hop.resident_name}
            </a>
            <span className="lineage-title">{hop.title}</span>
            {hop.sender_name === null ? (
              <small>where the chain started</small>
            ) : (
              <small>written by {hop.sender_name}</small>
            )}
            {hop.state && <LetterStateChip state={hop.state} />}
          </li>
        ))}
      </ol>
    </nav>
  );
}
