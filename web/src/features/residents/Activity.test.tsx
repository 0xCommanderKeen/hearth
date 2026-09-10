// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Client, type ResidentActivity } from "../../shared/client";
import { ResidentActivityLog } from "./Activity";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const client = new Client("synthetic-token");
const page: ResidentActivity = {
  entries: [
    {
      sequence: 20,
      at: 1789069096,
      kind: "run.interrupted",
      resource_id: "run-one",
      run_id: "run-one",
      run_status: "interrupted",
      runtime_kind: "codex_subscription",
      diagnostic: {
        code: "sandbox_termination_unknown",
        message: "The container outcome could not be confirmed.",
        turn_started: false,
      },
    },
  ],
  next_before: 20,
};
const props = {
  client,
  residentId: "karen",
  residentName: "Karen",
  cursor: 20,
  connected: true,
};

it("shows the interruption explanation and pages the resident's durable history", async () => {
  const read = vi
    .spyOn(client, "activity")
    .mockResolvedValueOnce(page)
    .mockResolvedValueOnce({ entries: [], next_before: null });
  render(<ResidentActivityLog {...props} />);
  expect(await screen.findByText("Outcome unknown")).toBeTruthy();
  expect(
    screen.getByText("No model turn was recorded as started."),
  ).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Open run →" }).getAttribute("href"),
  ).toBe("#runs/run-one");
  fireEvent.click(screen.getByRole("button", { name: "Older activity" }));
  expect(await screen.findByText("No recorded activity yet.")).toBeTruthy();
  expect(read).toHaveBeenLastCalledWith("karen", 20);
});

it("refreshes on new activity and reports failures without claiming no events", async () => {
  const read = vi.spyOn(client, "activity").mockResolvedValue(page);
  const view = render(<ResidentActivityLog {...props} />);
  await screen.findByText("Outcome unknown");
  read.mockRejectedValueOnce(new Error("unavailable"));
  view.rerender(<ResidentActivityLog {...props} cursor={21} />);
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByText("Outcome unknown")).toBeTruthy();
  view.rerender(
    <ResidentActivityLog {...props} cursor={21} connected={false} />,
  );
  expect(screen.getByText(/Disconnected/)).toBeTruthy();
});

it("ignores a late response when the resident page changes", async () => {
  let resolve!: (value: ResidentActivity) => void;
  const read = vi
    .spyOn(client, "activity")
    .mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    )
    .mockResolvedValue({ entries: [], next_before: null });
  const view = render(<ResidentActivityLog key="karen" {...props} />);
  await waitFor(() => expect(read).toHaveBeenCalled());
  view.rerender(
    <ResidentActivityLog
      key="other"
      {...props}
      residentId="other"
      residentName="Other"
    />,
  );
  await screen.findByText("No recorded activity yet.");
  await act(async () => resolve(page));
  expect(screen.queryByText("Outcome unknown")).toBeNull();
  expect(
    screen.getByRole("region", { name: "Activity for Other" }),
  ).toBeTruthy();
});
