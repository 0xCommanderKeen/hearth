// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Client, type Task } from "../../shared/client";
import { TaskInstruction } from "./TaskInstruction";
afterEach(cleanup);
const task: Task = {
  id: "one",
  resident_id: "reader",
  instruction: "Preview",
  instruction_truncated: true,
  status: "queued",
  created_at: 1,
};

it("loads complete instructions on demand, retries a failed read, and reuses a successful read", async () => {
  const client = new Client("synthetic");
  const read = vi
    .spyOn(client, "task")
    .mockRejectedValueOnce(new Error("Unavailable"))
    .mockResolvedValue({
      ...task,
      instruction: "Full 🦔 instructions\ncontinued",
    });
  render(<TaskInstruction client={client} task={task} />);
  expect(read).not.toHaveBeenCalled();
  expect(screen.getByRole("heading").textContent).toBe("Preview…");
  fireEvent.click(
    screen.getByRole("button", { name: "Read full instructions" }),
  );
  await screen.findByRole("alert");
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toBe("Unavailable"),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Read full instructions" }),
  );
  await screen.findByRole("button", { name: "Hide full instructions" });
  expect(screen.getByText(/Full 🦔 instructions/).textContent).toBe(
    "Full 🦔 instructions\ncontinued",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Hide full instructions" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Read full instructions" }),
  );
  expect(read).toHaveBeenCalledTimes(2);
});

it("discards old client responses and clears cached content when the session changes", async () => {
  const first = new Client("first"),
    second = new Client("second");
  let finish!: (task: Task) => void;
  vi.spyOn(first, "task").mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  vi.spyOn(second, "task").mockResolvedValue({
    ...task,
    instruction: "New household",
  });
  const view = render(<TaskInstruction client={first} task={task} />);
  fireEvent.click(
    screen.getByRole("button", { name: "Read full instructions" }),
  );
  view.rerender(<TaskInstruction client={second} task={task} />);
  finish({ ...task, instruction: "Old household" });
  fireEvent.click(
    screen.getByRole("button", { name: "Read full instructions" }),
  );
  await screen.findByText("New household");
  expect(screen.queryByText("Old household")).toBeNull();
  view.rerender(<TaskInstruction client={first} task={task} />);
  expect(screen.queryByText("New household")).toBeNull();
});
