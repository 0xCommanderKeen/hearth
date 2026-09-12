import type { Resident, Run, Snapshot } from "../../../shared/client";

// Non-resident IDs reserve ordinary plots without moving existing homes. They
// cannot collide with a valid resident ID and use the same disposable allocator.
export const PLACES = [
  {
    id: "@workshop",
    key: "workshop",
    identity: "#hamlet/workshop",
    name: "Workshop",
    kind: "workshop",
    description: "A place for running work and recorded authoring actions.",
    href: "#tasks",
    link: "Tasks & results",
  },
  {
    id: "@research",
    key: "research",
    identity: "#hamlet/research",
    name: "Research House",
    kind: "research",
    description: "A place for recorded catalog, memory and source reads.",
    href: "#inputs",
    link: "Inputs & sources",
  },
  {
    id: "@post",
    key: "post",
    identity: "#hamlet/post",
    name: "Post Office",
    kind: "post",
    description:
      "A place for letters and queued announcements. Postal couriers carry newly observed letters.",
    href: "#inbox",
    link: "Inbox",
  },
] as const;
export type Place = (typeof PLACES)[number];
export const placeByIdentity = (identity: string) =>
  PLACES.find((p) => p.identity === identity);
export type Location = { destination: string | null; label: string; run?: Run };

export function residentLocation(
  resident: Resident,
  snapshot: Snapshot,
): Location {
  const home = { destination: resident.id, label: "At home" };
  if (resident.lifecycle?.state === "archived")
    return { ...home, label: "Archived" };
  if (["idle", "ready", "paused"].includes(resident.presence)) return home;
  if (["claimed", "starting"].includes(resident.presence))
    return { ...home, label: "Preparing at home" };
  // Uncertain/stopping execution is never reinterpreted as useful work.
  const run = (snapshot.runs ?? []).find(
    (r) =>
      r.resident_id === resident.id &&
      r.status === "running" &&
      !r.cancellation_requested,
  );
  if (resident.presence !== "running" || !run)
    return { ...home, label: "Location unknown" };
  const action = run.action;
  const destination =
    action?.place === "townhall"
      ? null
      : (PLACES.find((p) => p.key === action?.place)?.id ?? "@workshop");
  return {
    destination,
    label:
      destination === null
        ? "Townhall"
        : PLACES.find((p) => p.id === destination)!.name,
    run,
  };
}

export type Visit = {
  residentId: string;
  from: string | null;
  to: string | null;
  runId?: string;
};
export function createResidentJourneys() {
  let previous = new Map<string, Location>();
  return {
    observe(snapshot: Snapshot, animate: boolean): Visit[] {
      const next = new Map(
        snapshot.residents
          .filter((r) => r.lifecycle?.state !== "archived")
          .map((r) => [r.id, residentLocation(r, snapshot)]),
      );
      const moves: Visit[] = [];
      for (const [id, location] of next) {
        const before = previous.get(id);
        if (
          animate &&
          before &&
          before.destination !== location.destination &&
          location.label !== "Location unknown" &&
          before.label !== "Location unknown"
        ) {
          moves.push({
            residentId: id,
            from: before.destination,
            to: location.destination,
            runId: location.run?.id,
          });
        }
      }
      previous = next;
      return moves.slice(0, 12);
    },
    current(visit: Visit) {
      const at = previous.get(visit.residentId);
      return at?.destination === visit.to && at?.run?.id === visit.runId;
    },
  };
}
