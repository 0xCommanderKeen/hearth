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
import { Client, RequestError, type Snapshot } from "../../shared/client";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const state: Snapshot = {
  schema_version: 1,
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
  runtimes: {
    default: "codex_subscription",
    configured: ["codex_subscription"],
    kinds: { codex_subscription: { label: "Codex subscription", live: true } },
  },
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
  fireEvent.change(screen.getByLabelText("The daily assignment"), {
    target: { value: "Summarize the day." },
  });
  fireEvent.change(screen.getByLabelText("Daily time"), {
    target: { value: "07:45" },
  });
  fireEvent.change(screen.getByLabelText("Timezone"), {
    target: { value: "UTC" },
  });
  fireEvent.click(screen.getByText("Schedule daily routine"));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        resident_id: "reader",
        instruction: "Summarize the day.",
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

it("reuses uncertain identity and reconciles the matching routine", async () => {
  const client = new Client("synthetic-test-token");
  const save = vi
    .spyOn(client, "saveRoutine")
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockRejectedValueOnce(new RequestError(409, "revision conflict"));
  const read = vi.spyOn(client, "state").mockImplementation(async () => ({
    ...state,
    routines: [
      {
        ...save.mock.calls[0][1],
        id: save.mock.calls[0][0],
        revision: 1,
        enabled: 1,
        next_at: 123,
      },
    ],
  }));
  render(
    <RoutinePanel
      client={client}
      snapshot={state}
      busy={false}
      act={async (op) => {
        await op().catch(() => {});
      }}
    />,
  );
  fireEvent.change(screen.getByLabelText("The daily assignment"), {
    target: { value: "Synthetic" },
  });
  fireEvent.click(screen.getByText("Schedule daily routine"));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByText("Schedule daily routine"));
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  expect(save.mock.calls[1]).toEqual(save.mock.calls[0]);
  await waitFor(() =>
    expect(
      (screen.getByLabelText("The daily assignment") as HTMLTextAreaElement)
        .value,
    ).toBe(""),
  );
});

it("preserves a concurrent edit and gives changed payloads and new schedules distinct identities", async () => {
  const client = new Client("synthetic-test-token");
  const save = vi
    .spyOn(client, "saveRoutine")
    .mockRejectedValue(new RequestError(409, "revision conflict"));
  vi.spyOn(client, "state").mockImplementation(async () => ({
    ...state,
    routines: [
      {
        ...save.mock.calls[0][1],
        id: save.mock.calls[0][0],
        revision: 2,
        enabled: 0,
        next_at: 123,
      },
    ],
  }));
  const errors: unknown[] = [];
  render(
    <RoutinePanel
      client={client}
      snapshot={state}
      busy={false}
      act={async (op) => {
        await op().catch((e) => errors.push(e));
      }}
    />,
  );
  const input = screen.getByLabelText("The daily assignment");
  const submit = screen.getByText("Schedule daily routine");
  fireEvent.change(input, { target: { value: "Synthetic" } });
  fireEvent.click(submit);
  await waitFor(() => expect(errors).toHaveLength(1));
  fireEvent.click(submit);
  await waitFor(() => expect(errors).toHaveLength(2));
  expect(save.mock.calls[1]).toEqual(save.mock.calls[0]);
  expect((input as HTMLTextAreaElement).value).toBe("Synthetic");
  save.mockResolvedValue({});
  fireEvent.change(input, { target: { value: "Different assignment" } });
  fireEvent.click(submit);
  await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(""));
  expect(save.mock.calls[2][0]).not.toBe(save.mock.calls[0][0]);
  fireEvent.change(input, { target: { value: "Different assignment" } });
  fireEvent.click(submit);
  await waitFor(() => expect(save).toHaveBeenCalledTimes(4));
  expect(save.mock.calls[3][0]).not.toBe(save.mock.calls[2][0]);
});
