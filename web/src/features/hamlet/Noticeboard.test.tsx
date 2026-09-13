// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { Noticeboard } from "./Noticeboard";
import type { Snapshot } from "../../shared/client";
afterEach(cleanup);
it("uses current task outcomes, preserves archived holds and links completed runs without retained tasks", () => {
  const snapshot = {
    residents: [
      { id: "one", name: "One" },
      {
        id: "old",
        name: "Archived",
        unresolved_runs: 2,
        lifecycle: { state: "archived" },
      },
    ],
    tasks: [
      {
        id: "retry",
        resident_id: "one",
        status: "succeeded",
        instruction: "Retried successfully",
      },
      {
        id: "unknown",
        resident_id: "one",
        status: "interrupted",
        instruction: "Inspect this",
      },
    ],
    runs: [
      {
        id: "old-failure",
        task_id: "retry",
        resident_id: "one",
        status: "failed",
      },
      {
        id: "done/1",
        task_id: "gone",
        resident_id: "one",
        status: "succeeded",
        finished_at: 100,
      },
    ],
    letters: [],
  } as unknown as Snapshot;
  render(<Noticeboard snapshot={snapshot} connected={false} />);
  expect(screen.getByText(/Disconnected/)).toBeTruthy();
  const attention = within(
    screen.getByRole("region", { name: "Work needing attention" }),
  );
  expect(attention.getByText(/Outcome unknown/)).toBeTruthy();
  expect(attention.getByText(/Archived resident/)).toBeTruthy();
  expect(attention.queryByText(/Task failed/)).toBeNull();
  expect(
    screen
      .getByRole("link", { name: "One · Run completed" })
      .getAttribute("href"),
  ).toBe("#runs/done%2F1");
  expect(screen.getByText(/Task details are outside/)).toBeTruthy();
});
it("bounds and orders retained letters, handles Townhall and replaces records on reset", () => {
  const snapshot = {
    residents: [],
    tasks: [],
    runs: [],
    letters: Array.from({ length: 8 }, (_, i) => ({
      kind: "letter_sent",
      task_id: String(i),
      from_resident_id: null,
      to_resident_id: null,
      at: i,
      title: `Letter ${i}`,
    })),
  } as unknown as Snapshot;
  const { rerender } = render(<Noticeboard snapshot={snapshot} connected />);
  const letters = within(
    screen.getByRole("region", { name: "Letters on the noticeboard" }),
  );
  expect(letters.getAllByRole("listitem")).toHaveLength(4);
  expect(letters.getAllByRole("listitem")[0].textContent).toContain("Letter 7");
  expect(letters.getAllByRole("link")[0].getAttribute("href")).toBe("#inbox");
  rerender(<Noticeboard snapshot={{ ...snapshot, letters: [] }} connected />);
  expect(screen.queryByText(/Letter 7/)).toBeNull();
  expect(screen.getByText("No letters in these records.")).toBeTruthy();
});

it("prioritizes unresolved holds and exposes attention items beyond the four-card limit", () => {
  const snapshot = {
    residents: [
      {
        id: "old",
        name: "Archived",
        unresolved_runs: 2,
        lifecycle: { state: "archived" },
      },
    ],
    tasks: Array.from({ length: 6 }, (_, i) => ({
      id: String(i),
      resident_id: "old",
      status: "failed",
      instruction: `Failure ${i}`,
    })),
    runs: [],
    letters: [],
  } as unknown as Snapshot;
  render(<Noticeboard snapshot={snapshot} connected />);
  const attention = within(
    screen.getByRole("region", { name: "Work needing attention" }),
  );
  expect(attention.getAllByRole("listitem")).toHaveLength(4);
  expect(attention.getAllByRole("listitem")[0].textContent).toContain(
    "2 unresolved run(s)",
  );
  fireEvent.click(
    attention.getByRole("button", { name: "Show all 7 attention items" }),
  );
  expect(attention.getAllByRole("listitem")).toHaveLength(7);
  expect(attention.getByText("Failure 5")).toBeTruthy();
});
