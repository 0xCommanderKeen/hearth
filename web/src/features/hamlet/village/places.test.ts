import { expect, it } from "vitest";
import type { Resident, Run, Snapshot } from "../../../shared/client";
import { createResidentJourneys, residentLocation, PLACES } from "./places";
import { createPlotAllocator } from "./layout";
const resident = {
  id: "reader",
  name: "Reader",
  presence: "ready",
} as Resident;
function state(presence = "ready", place?: "research" | "post" | "workshop") {
  return {
    residents: [{ ...resident, presence }],
    runs:
      presence === "running"
        ? [
            {
              id: "run",
              resident_id: resident.id,
              status: "running",
              action: place ? { place } : null,
            },
          ]
        : [],
    tasks: [{ instruction: "research post workshop" }],
  } as Snapshot;
}
it("keeps idle and ready residents home and never guesses intent from task text", () => {
  for (const presence of ["ready", "idle", "paused"])
    expect(
      residentLocation({ ...resident, presence }, state(presence)).destination,
    ).toBe("reader");
  const running = state("running");
  expect(residentLocation(running.residents[0], running).destination).toBe(
    "@workshop",
  );
  running.runs[0].action = {
    place: "research",
    label: "Read memory",
    at: 1,
    sequence: 2,
  };
  expect(residentLocation(running.residents[0], running).destination).toBe(
    "@research",
  );
  running.runs[0].cancellation_requested = 1;
  expect(residentLocation(running.residents[0], running).label).toBe(
    "Location unknown",
  );
});
it("walks once between recorded places and home; reset/hidden/reduced motion consume in place", () => {
  const moves = createResidentJourneys();
  expect(moves.observe(state(), false)).toEqual([]);
  expect(moves.observe(state("starting"), true)).toEqual([]);
  const research = moves.observe(state("running", "research"), true);
  expect(research).toEqual([
    { residentId: "reader", from: "reader", to: "@research", runId: "run" },
  ]);
  expect(moves.observe(state("running", "research"), true)).toEqual([]);
  expect(moves.observe(state("running", "post"), true)[0].to).toBe("@post");
  expect(moves.current(research[0])).toBe(false);
  expect(moves.observe(state(), true)[0]).toMatchObject({
    from: "@post",
    to: "reader",
  });
  expect(moves.observe(state("running", "research"), false)).toEqual([]);
  expect(moves.observe(state("running", "research"), true)).toEqual([]);
});
it("does not animate unavailable, archived or unobserved identities", () => {
  const moves = createResidentJourneys();
  moves.observe(state("running", "post"), false);
  expect(moves.observe(state("interrupted"), true)).toEqual([]);
  expect(moves.observe(state(), true)).toEqual([]);
  const archived = state("running", "research");
  archived.residents[0].lifecycle = {
    state: "archived",
    resident_id: "reader",
  };
  expect(moves.observe(archived, true)).toEqual([]);
  expect(moves.observe(state("running", "research"), true)).toEqual([]);
});
it("reserves civic plots without moving saved homes", () => {
  const allocate = createPlotAllocator("one");
  const before = allocate(["reader", "keeper"]);
  const after = allocate(["reader", "keeper", ...PLACES.map((p) => p.id)]);
  expect(after.slice(0, 2)).toEqual(before);
  expect(new Set(after.map((p) => `${p.x},${p.z}`)).size).toBe(5);
});
it("bounds simultaneous arrivals and invalidates a visit when its run is replaced", () => {
  const moves = createResidentJourneys();
  const many = {
    residents: Array.from({ length: 100 }, (_, i) => ({
      ...resident,
      id: String(i),
    })),
    runs: [],
  } as unknown as Snapshot;
  moves.observe(many, false);
  many.residents = many.residents.map((r) => ({ ...r, presence: "running" }));
  many.runs = many.residents.map(
    (r) => ({ id: `run-${r.id}`, resident_id: r.id, status: "running" }) as Run,
  );
  const visits = moves.observe(many, true);
  expect(visits).toHaveLength(12);
  many.runs[0].id = "replacement";
  moves.observe(many, true);
  expect(moves.current(visits[0])).toBe(false);
});
