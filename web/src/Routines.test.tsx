// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { RoutinePanel } from "./Routines";
import { Client, type Snapshot } from "./client";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const state: Snapshot = {
  schema_version: 1,
  simulated: true,
  epoch: "test",
  cursor: 0,
  residents: [
    {
      id: "reader",
      name: "Reader",
      purpose: "Synthetic",
      revision: 1,
      daily_limit: 10000,
      presence: "ready",
      pause_reason: null,
    },
  ],
  tasks: [],
  runs: [],
  activity: [],
};
const act = async (operation: () => Promise<unknown>) => {
  await operation();
};

it("saves the selected local schedule with an initial revision", async () => {
  const client = new Client("synthetic-test-token");
  const save = vi.spyOn(client, "saveRoutine").mockResolvedValue({});
  render(
    <RoutinePanel client={client} snapshot={state} busy={false} act={act} />,
  );
  fireEvent.change(screen.getByLabelText("Daily time"), {
    target: { value: "07:45" },
  });
  fireEvent.change(screen.getByLabelText("Timezone"), {
    target: { value: "UTC" },
  });
  fireEvent.click(screen.getByText("Enable daily mock summary"));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      "reader-daily",
      expect.objectContaining({
        local_time: "07:45",
        timezone: "UTC",
        enabled: true,
        expected_revision: 0,
      }),
    ),
  );
});

it("disables the displayed revision and explains existing tasks", async () => {
  const client = new Client("synthetic-test-token");
  const save = vi.spyOn(client, "saveRoutine").mockResolvedValue({});
  render(
    <RoutinePanel
      client={client}
      snapshot={{
        ...state,
        routines: [
          {
            id: "reader-daily",
            resident_id: "reader",
            revision: 4,
            enabled: 1,
            next_at: 2_000_000_000,
            local_time: "09:00",
            timezone: "UTC",
            instruction: "Synthetic",
          },
        ],
      }}
      busy={false}
      act={act}
    />,
  );
  expect(screen.getByText(/Tasks already queued/)).toBeTruthy();
  fireEvent.click(screen.getByText("Disable daily routine"));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      "reader-daily",
      expect.objectContaining({
        enabled: false,
        expected_revision: 4,
      }),
    ),
  );
});
