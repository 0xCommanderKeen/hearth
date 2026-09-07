import { afterEach, describe, expect, it, vi } from "vitest";
import { Client, decodeSnapshot, RequestError } from "./client";

afterEach(() => vi.restoreAllMocks());

describe("same-origin operator interface", () => {
  it("rejects incompatible or non-simulated state before display", () => {
    expect(() => decodeSnapshot({ schema_version: 2 })).toThrow("state format");
    expect(() =>
      decodeSnapshot({ schema_version: 1, simulated: false }),
    ).toThrow("state format");
  });
  it("never forwards credentials to external paths", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch");
    const client = new Client("synthetic-token");
    await expect(
      client.request("https://example.invalid/api/state"),
    ).rejects.toThrow("local API path");
    await expect(client.request("//example.invalid/api/state")).rejects.toThrow(
      "local API path",
    );
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("keeps command identity and payload stable on lost-response retry", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch");
    fetcher.mockRejectedValueOnce(new TypeError("network failed"));
    fetcher.mockResolvedValueOnce(
      new Response(JSON.stringify({ command_id: "same", task_id: "task" })),
    );
    const pending = {
      id: "same",
      body: {
        resident_id: "reader",
        instruction: "Synthetic",
        expires_at: 123,
      },
    };
    const client = new Client("synthetic-token");
    await expect(client.submit(pending)).rejects.toThrow("network failed");
    expect(await client.submit(pending)).toEqual({
      command_id: "same",
      task_id: "task",
    });
    expect(fetcher.mock.calls[0]).toEqual(fetcher.mock.calls[1]);
  });
  it("clears a rejected credential before another request", async () => {
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(new Response("{}"));
    const client = new Client("synthetic-token");
    await expect(client.request("/api/state")).rejects.toBeInstanceOf(
      RequestError,
    );
    await client.request("/api/state");
    expect(fetcher.mock.calls[1][1]?.headers).toMatchObject({
      Authorization: "Bearer ",
    });
    expect(fetcher.mock.calls[1][1]?.redirect).toBe("error");
  });
  it("rejects a receipt that belongs to a different command", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ command_id: "wrong", task_id: "task" })),
    );
    await expect(
      new Client("synthetic").submit({
        id: "right",
        body: {
          resident_id: "reader",
          instruction: "Synthetic",
          expires_at: 123,
        },
      }),
    ).rejects.toThrow("receipt is incomplete");
  });
});

it("reconciles an accepted command after its submission deadline expires", async () => {
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "invalid_command_deadline" }), {
        status: 409,
      }),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({ command_id: "old", task_id: "accepted-task" }),
      ),
    );
  const receipt = await new Client("synthetic").submit({
    id: "old",
    body: { resident_id: "reader", instruction: "Original", expires_at: 1 },
  });
  expect(receipt.task_id).toBe("accepted-task");
  expect(fetcher.mock.calls[1][0]).toBe("/api/commands/old");
});

it("allows a fresh command when an expired submission was never accepted", async () => {
  vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "invalid_command_deadline" }), {
        status: 409,
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "command_not_found" }), {
        status: 404,
      }),
    );
  await expect(
    new Client("synthetic").submit({
      id: "old",
      body: { resident_id: "reader", instruction: "Original", expires_at: 1 },
    }),
  ).rejects.toMatchObject({ status: 410 });
});

it("keeps restored-state reads available but refuses mutations before fetch", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(
    async () =>
      new Response(
        JSON.stringify({
          schema_version: 1,
          simulated: true,
          epoch: "restored",
          cursor: 1,
          restore_hold: true,
          residents: [],
          tasks: [],
          runs: [],
          activity: [],
        }),
      ),
  );
  const client = new Client("synthetic-test-token");
  await client.state();
  await expect(client.bootstrapManagement()).rejects.toThrow("read-only");
  expect(fetcher).toHaveBeenCalledTimes(1);
  await client.state();
  expect(fetcher).toHaveBeenCalledTimes(2);
});
