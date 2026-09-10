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

it("delivers budget rollover at the same audit cursor and sends its initial budget identity", async () => {
  const client = new Client("synthetic");
  const abort = new AbortController();
  const before = { ...state(10), budget_revision: "before" };
  const after = { ...state(10), budget_revision: "after" };
  const delivered: Snapshot[] = [];
  vi.spyOn(client, "state").mockResolvedValue(before);
  const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(
              `event: snapshot\ndata: ${JSON.stringify(after)}\n\n`,
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
      if (delivered.length === 2) abort.abort();
    },
    () => {},
  );
  expect(fetcher.mock.calls[0][0]).toBe(
    "/api/events?cursor=10&epoch=one&budget_revision=before",
  );
  expect(delivered.map((s) => s.budget_revision)).toEqual(["before", "after"]);
  expect(delivered.map((s) => s.cursor)).toEqual([10, 10]);
  expect(streamBaseline(delivered[0])).toBe(streamBaseline(delivered[1]));
});

it("accepts multiple bounded frames in a chunk larger than the frame limit", async () => {
  const client = new Client("synthetic");
  const abort = new AbortController();
  const delivered: number[] = [];
  vi.spyOn(client, "state").mockResolvedValue(state(0));
  const frames = [1, 2, 3]
    .map(
      (cursor) =>
        `data: ${JSON.stringify({ ...state(cursor), padding: "a".repeat(900_000) })}\n\n`,
    )
    .join("");
  expect(frames.length).toBeGreaterThan(2_000_000);
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(frames));
        },
      }),
    ),
  );
  await client.watch(
    abort.signal,
    (s) => {
      delivered.push(s.cursor);
      if (s.cursor === 3) abort.abort();
    },
    (connected) => {
      if (!connected) abort.abort();
    },
  );
  expect(delivered).toEqual([0, 1, 2, 3]);
});

it.each([false, true])(
  "refuses an oversized %s completed frame even across chunks",
  async (complete) => {
    const client = new Client("synthetic");
    const abort = new AbortController();
    const delivered: number[] = [];
    vi.spyOn(client, "state").mockResolvedValue(state(0));
    const frame = `data: ${JSON.stringify({ ...state(1), padding: "a".repeat(2_000_000) })}${complete ? "\n\n" : ""}`;
    const cancel = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            const bytes = new TextEncoder().encode(frame);
            controller.enqueue(bytes.slice(0, 1_000_000));
            controller.enqueue(bytes.slice(1_000_000));
          },
          cancel,
        }),
      ),
    );
    await client.watch(
      abort.signal,
      (s) => delivered.push(s.cursor),
      (connected) => {
        if (!connected) abort.abort();
      },
    );
    expect(delivered).toEqual([0]);
    expect(cancel).toHaveBeenCalledOnce();
  },
);

it("preserves Unicode and frame separators fragmented one byte at a time", async () => {
  const client = new Client("synthetic");
  const abort = new AbortController();
  const delivered: Snapshot[] = [];
  vi.spyOn(client, "state").mockResolvedValue(state(0));
  const next = {
    ...state(1),
    tasks: [
      {
        id: "unicode",
        instruction: '🦔漢\nquote"',
        resident_id: "reader",
        status: "queued",
        created_at: 1,
      },
    ],
  };
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      new ReadableStream({
        start(controller) {
          const bytes = new TextEncoder().encode(
            `: keepalive\n\nevent: snapshot\ndata: ${JSON.stringify(next)}\n\n`,
          );
          for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
        },
      }),
    ),
  );
  await client.watch(
    abort.signal,
    (s) => {
      delivered.push(s);
      if (s.cursor === 1) abort.abort();
    },
    () => {},
  );
  expect(delivered[1]).toEqual(next);
});

it("accepts a frame exactly at the bound when its final delimiter is split", async () => {
  const client = new Client("synthetic");
  const abort = new AbortController();
  const delivered: number[] = [];
  vi.spyOn(client, "state").mockResolvedValue(state(0));
  const prefix = `data: ${JSON.stringify(state(1))}\n: `;
  const frame = prefix + "a".repeat(2_000_000 - prefix.length);
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(frame + "\n"));
          controller.enqueue(new TextEncoder().encode("\n"));
        },
      }),
    ),
  );
  await client.watch(
    abort.signal,
    (s) => {
      delivered.push(s.cursor);
      if (s.cursor === 1) abort.abort();
    },
    (connected) => {
      if (!connected) abort.abort();
    },
  );
  expect(delivered).toEqual([0, 1]);
});
