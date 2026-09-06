// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Approvals } from "./Approvals";
import { Client, type Approval, type Snapshot } from "./client";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const proposal: Approval = {
  id: "review",
  artifact_id: "result",
  resident_id: "reader",
  digest: "a".repeat(64),
  expires_at: 2_000_000_000,
  status: "pending",
  payload: {
    action: "mock.publish",
    destination: "mock-noticeboard",
    sha256: "b".repeat(64),
    resident_revision: 1,
    policy_revision: 2,
    destination_revision: 3,
  },
};
const state: Snapshot = {
  schema_version: 1,
  simulated: true,
  epoch: "test",
  cursor: 1,
  residents: [],
  tasks: [],
  runs: [],
  activity: [],
  approvals: [proposal],
  actions: [],
};
const run = async (operation: () => Promise<unknown>) => {
  await operation();
};

it("requires exact preview before sending the reviewed digest", async () => {
  const client = new Client("synthetic-test-token");
  vi.spyOn(client, "review").mockResolvedValue({
    approval: proposal,
    content: "Exact synthetic summary",
  });
  const decide = vi
    .spyOn(client, "decide")
    .mockResolvedValue({ ...proposal, status: "approved" });
  render(<Approvals client={client} snapshot={state} busy={false} act={run} />);
  expect(screen.queryByText("Approve this exact mock action")).toBeNull();
  fireEvent.click(screen.getByText("Review exact summary"));
  await screen.findByText("Exact synthetic summary");
  expect(screen.getByText(/Resident revision 1/)).toBeTruthy();
  fireEvent.click(screen.getByText("Approve this exact mock action"));
  await waitFor(() => expect(decide).toHaveBeenCalledWith(proposal, true));
});

it("offers reconciliation for unknown actions and does not call publication on render", async () => {
  const client = new Client("synthetic-test-token");
  const execute = vi
    .spyOn(client, "execute")
    .mockResolvedValue({ status: "unknown" });
  render(
    <Approvals
      client={client}
      snapshot={{
        ...state,
        approvals: [{ ...proposal, status: "approved" }],
        actions: [
          { id: "review", status: "unknown", reason: "effect_unconfirmed" },
        ],
      }}
      busy={false}
      act={run}
    />,
  );
  expect(execute).not.toHaveBeenCalled();
  expect(screen.getByRole("status").textContent).toContain(
    "without sending again",
  );
  fireEvent.click(screen.getByText("Reconcile mock action"));
  await waitFor(() => expect(execute).toHaveBeenCalledWith("review"));
});

it("retains request identity and deadline across a lost acknowledgement", async () => {
  const client = new Client("synthetic-test-token");
  const propose = vi
    .spyOn(client, "propose")
    .mockRejectedValueOnce(new Error("lost ack"))
    .mockResolvedValue(proposal);
  const catchFailure = async (operation: () => Promise<unknown>) => {
    await operation().catch(() => {});
  };
  render(
    <Approvals
      client={client}
      snapshot={{
        ...state,
        publication_policies: [
          { resident_id: "reader", enabled: 1, revision: 1 },
        ],
        runs: [
          {
            id: "result",
            task_id: "task",
            resident_id: "reader",
            status: "succeeded",
            artifact_id: "result",
            actual_cost: 0,
            usage_known: 1,
            cancellation_requested: 0,
          },
        ],
      }}
      busy={false}
      act={catchFailure}
    />,
  );
  fireEvent.click(screen.getByText("Request review of latest summary"));
  await waitFor(() => expect(propose).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByText("Request review of latest summary"));
  await waitFor(() => expect(propose).toHaveBeenCalledTimes(2));
  expect(propose.mock.calls[0]).toEqual(propose.mock.calls[1]);
});

it("opens an approval link even when it is outside recent history", async () => {
  const client = new Client("synthetic-test-token");
  vi.spyOn(client, "review").mockResolvedValue({
    approval: proposal,
    content: "Older exact summary",
  });
  const decide = vi
    .spyOn(client, "decide")
    .mockResolvedValue({ ...proposal, status: "denied" });
  render(
    <Approvals
      client={client}
      snapshot={{ ...state, approvals: [] }}
      busy={false}
      act={run}
      linkedId="review"
    />,
  );
  await screen.findByText("Older exact summary");
  fireEvent.click(screen.getByText("Deny this mock action"));
  await waitFor(() => expect(decide).toHaveBeenCalledWith(proposal, false));
  await waitFor(() =>
    expect(
      (screen.getByText("Deny this mock action") as HTMLButtonElement).disabled,
    ).toBe(true),
  );
});
