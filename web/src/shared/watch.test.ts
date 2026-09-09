import { afterEach, expect, it, vi } from "vitest";
import { Client, streamBaseline, type Snapshot } from "./client";
afterEach(() => vi.restoreAllMocks());
const state = (cursor: number): Snapshot =>
  ({
    schema_version: 1,
    epoch: "one",
    cursor,
    residents: [],
    tasks: [],
    runs: [],
    activity: [],
    letters: [],
    runtimes: {
      default: "fixture",
      configured: ["fixture"],
      kinds: { fixture: { label: "Synthetic", live: false } },
    },
  }) as unknown as Snapshot;
it("carries connection/reset baselines on later frames even if renders skip their initial delivery", async () => {
  const client = new Client("synthetic");
  const abort = new AbortController();
  const delivered: Snapshot[] = [];
  vi.spyOn(client, "state").mockResolvedValue(state(10));
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(
              `data: ${JSON.stringify(state(11))}\n\nevent: reset\ndata: ${JSON.stringify(state(2))}\n\ndata: ${JSON.stringify(state(3))}\n\n`,
            ),
          );
        },
      }),
    ),
  );
  await client.watch(
    abort.signal,
    (s) => {
      delivered.push(s);
      if (s.cursor === 3) abort.abort();
    },
    () => {},
  );
  expect(delivered.map((s) => s.cursor)).toEqual([10, 11, 2, 3]);
  expect(streamBaseline(delivered[0])).toBe(streamBaseline(delivered[1]));
  expect(streamBaseline(delivered[2])).toBe(streamBaseline(delivered[3]));
  expect(streamBaseline(delivered[3])?.cursor).toBe(2);
  expect(streamBaseline(delivered[3])).not.toBe(streamBaseline(delivered[0]));
  expect(streamBaseline({ ...delivered[3] })).toBeUndefined();
});

it("gives a same-cursor reconnect its own baseline before reporting live connection", async () => {
  const client = new Client("synthetic");
  const abort = new AbortController();
  const delivered: Snapshot[] = [];
  const order: string[] = [];
  vi.spyOn(client, "state").mockImplementation(async () => state(10));
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () =>
      new Response(
        new ReadableStream({
          start(controller) {
            controller.close();
          },
        }),
      ),
  );
  await client.watch(
    abort.signal,
    (s) => {
      delivered.push(s);
      order.push("state");
      if (delivered.length === 2) abort.abort();
    },
    (connected) => order.push(connected ? "connected" : "disconnected"),
  );
  expect(delivered).toHaveLength(2);
  expect(delivered.map((s) => s.cursor)).toEqual([10, 10]);
  expect(streamBaseline(delivered[0])).not.toBe(streamBaseline(delivered[1]));
  expect(order.slice(0, 4)).toEqual([
    "state",
    "connected",
    "disconnected",
    "state",
  ]);
});
