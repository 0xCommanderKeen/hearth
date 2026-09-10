// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ContextPanel } from "./Panels";
import type { Snapshot, Run } from "../../shared/client";
afterEach(cleanup);
it("selects results by completion time rather than snapshot priority and exposes absent task text and unknown usage", () => {
  const snapshot = {
    residents: [{ id: "r", name: "Reader", presence: "interrupted" }],
    tasks: [],
    runs: [
      {
        id: "old-held",
        resident_id: "r",
        status: "failed",
        artifact_id: "old",
        finished_at: 5,
        usage_known: 0,
      },
      {
        id: "active",
        resident_id: "r",
        task_id: "absent",
        status: "interrupted",
        artifact_id: null,
        usage_known: 0,
      },
      {
        id: "latest",
        resident_id: "r",
        status: "succeeded",
        artifact_id: "new",
        finished_at: 10,
        usage_known: 1,
      },
    ] as Run[],
    limits: { runs: 100, tasks: 100 },
  } as unknown as Snapshot;
  render(
    <ContextPanel
      identity="#residents/r"
      snapshot={snapshot}
      connected
      active
      onClose={() => {}}
    />,
  );
  expect(
    screen.getByRole("link", { name: "Read result →" }).getAttribute("href"),
  ).toBe("#runs/latest");
  expect(
    screen.getByRole("link", {
      name: /Outcome unknown.*instruction outside available records/,
    }),
  ).toBeTruthy();
  expect(
    screen.getByText(/Snapshot window: up to 100 tasks and 100 runs/),
  ).toBeTruthy();
});
it("does not replace an absent selected identity with a different resident", () => {
  render(
    <ContextPanel
      identity="#residents/gone"
      snapshot={{ residents: [{ id: "other", name: "Other" }] } as Snapshot}
      connected
      active
      onClose={() => {}}
    />,
  );
  expect(screen.getByText(/has not been replaced/)).toBeTruthy();
  expect(screen.queryByText("Other")).toBeNull();
});
it("reports household unknown usage as reserved money, not a count of runs", () => {
  render(
    <ContextPanel
      identity="#townhall"
      snapshot={
        {
          residents: [],
          household: {
            daily_limit: 10000000,
            remaining: 9000000,
            spent: 500000,
            reserved: 200000,
            unknown: 300000,
          },
        } as unknown as Snapshot
      }
      connected
      active
      onClose={() => {}}
    />,
  );
  expect(screen.getByText(/\$0.30 held for unknown usage/)).toBeTruthy();
});
