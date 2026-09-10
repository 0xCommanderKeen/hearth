import type {
  LetterEvent,
  Resident,
  Snapshot,
  StreamBaseline,
} from "../../../shared/client";

export const JOURNEY_LIMIT = 12;
const SAME_SECOND_LIMIT = 256;
export const letterKey = (event: LetterEvent) =>
  `${event.kind}:${event.task_id}`;

// A bounded visual consumer, not an event store. Old or ambiguous arrivals remain
// readable in Recent post. A timestamp watermark prevents forgotten/overflow events
// resurfacing as new work; a saturated second is conservatively text-only.
export function createActivity() {
  let epoch: string | undefined;
  let cursor = -1;
  let baseline: StreamBaseline | undefined;
  let connected = false;
  let watermark = -Infinity;
  let keys = new Set<string>();
  let queue: LetterEvent[] = [];
  function consume(events: LetterEvent[], animate: boolean) {
    for (const event of [...events].reverse()) {
      if (!Number.isFinite(event.at) || event.at < watermark) continue;
      if (event.at > watermark) {
        watermark = event.at;
        keys = new Set();
      }
      const key = letterKey(event);
      if (keys.has(key) || keys.size >= SAME_SECOND_LIMIT) continue;
      keys.add(key);
      if (animate && queue.length < JOURNEY_LIMIT) queue.push(event);
    }
  }
  return {
    observe(
      snapshot: Pick<Snapshot, "epoch" | "cursor" | "letters">,
      online: boolean,
      visible: boolean,
      reduced: boolean,
      delivery?: StreamBaseline,
    ) {
      const reset = epoch !== snapshot.epoch || snapshot.cursor < cursor;
      const reconnect = delivery ? baseline !== delivery : !connected;
      if (reset || reconnect) {
        queue = [];
        watermark = -Infinity;
        keys.clear();
        const seed =
          delivery &&
          delivery.epoch === snapshot.epoch &&
          delivery.cursor <= snapshot.cursor
            ? delivery
            : snapshot;
        consume(seed.letters ?? [], false);
      }
      if (!online || !visible || reduced) queue = [];
      consume(snapshot.letters ?? [], online && visible && !reduced);
      epoch = snapshot.epoch;
      cursor = snapshot.cursor;
      // Ordinary client.state refreshes do not replace the active stream identity.
      if (delivery) baseline = delivery;
      connected = online;
      return reset || reconnect || !online || !visible || reduced;
    },
    take() {
      return queue.shift();
    },
    clear() {
      queue = [];
    },
  };
}

export function residentStatus(resident: Resident, connected: boolean) {
  const presence = resident.presence || "unknown";
  const label =
    presence === "interrupted" ? "Outcome unknown (interrupted)" : presence;
  const text = connected ? label : `disconnected · last known: ${label}`;
  const tone = !connected
    ? "unknown"
    : ["running", "starting", "claimed"].includes(presence)
      ? "working"
      : ["failed", "interrupted", "unknown", "cancel_requested"].includes(
            presence,
          )
        ? "unknown"
        : presence === "paused"
          ? "paused"
          : presence === "ready"
            ? "ready"
            : "unknown";
  return { text, tone };
}
