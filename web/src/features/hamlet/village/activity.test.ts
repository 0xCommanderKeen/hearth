import { expect, it } from "vitest";
import type { LetterEvent, Resident } from "../../../shared/client";
import { createActivity, JOURNEY_LIMIT, residentStatus } from "./activity";
import { streetNetwork, routePosition } from "./layout";
const event = (id: string, at = 100): LetterEvent => ({
  kind: "letter_sent",
  task_id: id,
  at,
  from_resident_id: null,
  to_resident_id: "reader",
  title: id,
  state: "pending",
  root_task_id: id,
  depth: 0,
});
const snapshot = (letters: LetterEvent[], cursor = 1, epoch = "one") => ({
  letters,
  cursor,
  epoch,
});
const drain = (model: ReturnType<typeof createActivity>) => {
  const result = [];
  let next;
  while ((next = model.take())) result.push(next.task_id);
  return result;
};
it("baselines history and consumes repeated, hidden and reduced-motion events", () => {
  const m = createActivity();
  const old = event("old");
  m.observe(snapshot([old]), true, true, false);
  expect(drain(m)).toEqual([]);
  m.observe(snapshot([event("fresh"), old], 2), true, true, false);
  expect(drain(m)).toEqual(["fresh"]);
  m.observe(snapshot([event("fresh"), old], 3), true, true, false);
  expect(drain(m)).toEqual([]);
  m.observe(snapshot([event("hidden", 101)], 4), true, false, false);
  m.observe(snapshot([event("hidden", 101)], 4), true, true, false);
  expect(drain(m)).toEqual([]);
  m.observe(snapshot([event("reduced", 102)], 5), true, true, true);
  m.observe(snapshot([event("reduced", 102)], 5), true, true, false);
  expect(drain(m)).toEqual([]);
});
it("baselines resets and reconnects even when intermediate renders are skipped", () => {
  const m = createActivity();
  const initial = snapshot([event("old")]);
  m.observe(initial, true, true, false, initial);
  const offline = snapshot([event("offline", 101)], 3);
  m.observe(
    snapshot([event("live", 102), ...offline.letters], 4),
    true,
    true,
    false,
    offline,
  );
  expect(drain(m)).toEqual(["live"]);
  m.observe(
    snapshot([event("epoch", 103)], 5, "two"),
    true,
    true,
    false,
    offline,
  );
  expect(drain(m)).toEqual([]);
  m.observe(snapshot([event("reset", 104)], 1, "two"), true, true, false);
  expect(drain(m)).toEqual([]);
  m.observe(
    snapshot([event("disconnected", 105)], 2, "two"),
    false,
    true,
    false,
  );
  m.observe(
    snapshot([event("disconnected", 105)], 2, "two"),
    true,
    true,
    false,
  );
  expect(drain(m)).toEqual([]);
});
it("bounds queue and dedup without replaying overflow or resurfaced older history", () => {
  const m = createActivity();
  m.observe(snapshot([]), true, true, false);
  const events = Array.from({ length: 400 }, (_, i) =>
    event(String(i)),
  ).reverse();
  m.observe(snapshot(events, 2), true, true, false);
  expect(drain(m)).toHaveLength(JOURNEY_LIMIT);
  m.observe(snapshot(events, 3), true, true, false);
  expect(drain(m)).toEqual([]);
  m.observe(snapshot([event("later", 101)], 4), true, true, false);
  expect(drain(m)).toEqual(["later"]);
  m.observe(snapshot([event("0", 100)], 5), true, true, false);
  expect(drain(m)).toEqual([]);
});
it("routes actual doors through rendered streets, including Townhall and remote rows", () => {
  const network = streetNetwork([
    { id: "townhall", x: -12, z: 0 },
    { id: "reader", x: -6, z: 6 },
    { id: "keeper", x: 12, z: -12 },
  ]);
  for (const [from, to] of [
    [null, "reader"],
    ["reader", null],
    ["reader", "keeper"],
  ] as const) {
    const path = network.route(from, to)!;
    expect(path.length).toBeGreaterThan(2);
    for (let i = 0; i <= 100; i++) {
      const p = routePosition(path, i / 100);
      expect(
        network.streets.some(
          (s) =>
            Math.abs(p.x - s.x) <= s.width / 2 + 0.001 &&
            Math.abs(p.z - s.z) <= s.depth / 2 + 0.001,
        ),
      ).toBe(true);
    }
  }
  expect(network.route("townhall", "reader")?.[0]).toEqual({ x: -12, z: 2.2 });
  expect(network.route("missing", "reader")).toBeNull();
  expect(network.route("reader", "reader")).toBeNull();
  expect(network.route(null, "reader")?.[0]).toEqual({ x: 0, z: -3.8 });
});
it.each(["unknown", "failed", "paused", "interrupted", "running", "ready"])(
  "keeps recorded %s in text and connection context",
  (presence) => {
    const r = { presence } as Resident;
    const label =
      presence === "interrupted" ? "Outcome unknown (interrupted)" : presence;
    expect(residentStatus(r, true).text).toBe(label);
    expect(residentStatus(r, false).text).toBe(
      `disconnected · last known: ${label}`,
    );
  },
);

it("admits live events after a batched explicit reset baseline", () => {
  const model = createActivity();
  model.observe(snapshot([event("before")], 10), true, true, false);
  const reset = snapshot([event("retained", 101)], 1);
  model.observe(
    snapshot([event("after", 102), ...reset.letters], 2),
    true,
    true,
    false,
    reset,
  );
  expect(drain(model)).toEqual(["after"]);
});

it("keeps stream dedup across an ordinary command refresh", () => {
  const model = createActivity();
  const initial = snapshot([event("old")]);
  model.observe(initial, true, true, false, initial);
  const next = snapshot([event("fresh", 101), ...initial.letters], 2);
  model.observe(next, true, true, false, initial);
  expect(drain(model)).toEqual(["fresh"]);
  model.observe(next, true, true, false); // App.act -> client.state()
  model.observe({ ...next, cursor: 3 }, true, true, false, initial);
  expect(drain(model)).toEqual([]);
});
