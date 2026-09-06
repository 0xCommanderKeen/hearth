// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HouseholdPanel } from "./Household";
import { Client } from "../../shared/client";
afterEach(cleanup);
it("shows shared holds and preserves a policy draft on a conflicting save", async () => {
  const client = new Client("synthetic-operator-token");
  vi.spyOn(client, "request").mockRejectedValue(new Error("revision conflict"));
  render(
    <HouseholdPanel
      client={client}
      policy={{
        revision: 1,
        daily_limit: 10000000,
        timezone: "Europe/Ljubljana",
        resident_limit: 20,
        concurrency_limit: 2,
        resident_count: 2,
        active_runs: 0,
        spent: 2000,
        reserved: 0,
        unknown: 10000,
        remaining: 9988000,
        budget_day: "2026-09-06",
      }}
      readOnly={false}
      onSaved={() => {}}
    />,
  );
  expect(screen.getByText("$0.0100")).toBeTruthy();
  fireEvent.click(screen.getByText("Edit household policy"));
  fireEvent.change(screen.getByLabelText("Daily allowance ($)"), {
    target: { value: "8" },
  });
  fireEvent.click(screen.getByText("Save policy"));
  await screen.findByText(/revision conflict/);
  expect(
    (screen.getByLabelText("Daily allowance ($)") as HTMLInputElement).value,
  ).toBe("8");
});
