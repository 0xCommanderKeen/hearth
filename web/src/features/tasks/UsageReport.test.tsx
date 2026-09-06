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
import { UsageReport } from "./UsageReport";

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
  fireEvent.change(screen.getByLabelText(/Mock cost/), {
    target: { value: "2500" },
  });
  fireEvent.change(screen.getByLabelText(/Evidence/), {
    target: { value: "Synthetic meter" },
  });
  fireEvent.click(screen.getByText("Record mock usage"));
  await waitFor(() => expect(report).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByText("Record mock usage"));
  await waitFor(() => expect(report).toHaveBeenCalledTimes(2));
  expect(report.mock.calls[1]).toEqual(report.mock.calls[0]);
  expect(report.mock.calls[0]).toEqual([
    "run",
    expect.any(String),
    2500,
    "Synthetic meter",
  ]);
});
