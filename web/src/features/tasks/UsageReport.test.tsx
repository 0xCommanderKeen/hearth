// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Client } from "../../shared/client";
import { UsageByOrigin, UsageReport } from "./UsageReport";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
it("retains exact amount, evidence and command identity after acknowledgement loss", async () => {
  const client = new Client("synthetic-test-token");
  const report = vi
    .spyOn(client, "reconcileUsage")
    .mockRejectedValueOnce(new Error("lost ack"))
    .mockResolvedValue({});
  const act = async (action: () => Promise<unknown>) => {
    await action().catch(() => {});
  };
  render(<UsageReport client={client} runId="run" busy={false} act={act} />);
  fireEvent.change(screen.getByLabelText(/Cost in micro-USD/), {
    target: { value: "2500" },
  });
  fireEvent.change(screen.getByLabelText(/Evidence/), {
    target: { value: "Synthetic meter" },
  });
  fireEvent.click(screen.getByText("Record usage"));
  await waitFor(() => expect(report).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByText("Record usage"));
  await waitFor(() => expect(report).toHaveBeenCalledTimes(2));
  expect(report.mock.calls[1]).toEqual(report.mock.calls[0]);
  expect(report.mock.calls[0]).toEqual([
    "run",
    expect.any(String),
    2500,
    "Synthetic meter",
  ]);
});

it("gathers one question's cost under its origin and names what is still unknown", async () => {
  const client = new Client("synthetic-test-token");
  const read = vi.spyOn(client, "usageByOrigin").mockResolvedValue({
    limit: 20,
    offset: 0,
    truncated: false,
    origins: [
      {
        root_task_id: "task-1",
        resident_id: "karen",
        instruction: "Ask the orchard reporter one question.",
        created_at: 1788640000,
        runs: 2,
        letters: 1,
        residents_involved: ["karen", "reporter"],
        known_cost: 4000,
        unknown_runs: 1,
        active_runs: 0,
        reserved: 0,
        started_at: 1788640000,
        last_at: 1788640100,
      },
    ],
  });
  const act = async (action: () => Promise<unknown>) => {
    await action();
  };
  render(<UsageByOrigin client={client} busy={false} act={act} />);
  fireEvent.click(screen.getByText("Read cost by origin"));
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  const entry = screen.getByLabelText("Cost by origin");
  expect(entry.textContent).toContain("Ask the orchard reporter one question.");
  expect(entry.textContent).toContain("0.0040 API-equivalent USD");
  expect(entry.textContent).toContain("2 runs");
  expect(entry.textContent).toContain("1 letter");
  expect(entry.textContent).toContain("karen, reporter");
  // Unknown usage is named, not folded into the amount.
  expect(entry.textContent).toContain("1 finished run has unknown usage");
});
