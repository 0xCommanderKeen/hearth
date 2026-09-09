// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { Hamlet } from "./Hamlet";
import { createVillageScene } from "./village/scene";
import type { Resident, Snapshot } from "../../shared/client";
vi.mock("./village/scene", () => ({ createVillageScene: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const resident = {
  id: "reader",
  name: "Reader",
  presence: "unknown",
} as Resident;
const snapshot = { residents: [resident], letters: [] } as unknown as Snapshot;
it("updates records and roster without recreating the renderer and disposes on unmount", () => {
  const scene = {
    active: vi.fn(),
    update: vi.fn(),
    select: vi.fn(),
    dispose: vi.fn(),
    zoom: vi.fn(),
    rotate: vi.fn(),
    overview: vi.fn(),
  };
  vi.mocked(createVillageScene).mockReturnValue(scene);
  const { rerender, unmount } = render(
    <Hamlet snapshot={snapshot} connected />,
  );
  rerender(
    <Hamlet
      snapshot={{
        ...snapshot,
        residents: [resident, { ...resident, id: "second" }],
      }}
      connected={false}
    />,
  );
  expect(createVillageScene).toHaveBeenCalledTimes(1);
  expect(scene.update).toHaveBeenCalledTimes(2);
  expect(screen.getAllByText(/disconnected/)).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(scene.zoom).toHaveBeenCalledWith(1.25);
  fireEvent.click(screen.getByRole("button", { name: "Rotate left" }));
  expect(scene.rotate).toHaveBeenCalledWith(-1);
  fireEvent.click(screen.getByRole("button", { name: "Overview" }));
  expect(scene.overview).toHaveBeenCalledOnce();
  unmount();
  expect(scene.dispose).toHaveBeenCalledOnce();
});
it("graphics failure leaves Townhall and truthful resident navigation available", () => {
  vi.mocked(createVillageScene).mockImplementation(() => {
    throw new Error("no graphics");
  });
  render(<Hamlet snapshot={snapshot} connected />);
  expect(screen.getByText(/3D is unavailable/)).toBeTruthy();
  expect(
    screen.getByRole("button", { name: /Select Reader/ }).textContent,
  ).toContain("unknown");
  expect(screen.getByRole("button", { name: "Select Townhall" })).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Zoom in" }).hasAttribute("disabled"),
  ).toBe(true);
});
it("shares selection between buildings and directory and retains an archived selection and clears a different store", () => {
  const scene = {
    active: vi.fn(),
    update: vi.fn(),
    select: vi.fn(),
    dispose: vi.fn(),
    zoom: vi.fn(),
    rotate: vi.fn(),
    overview: vi.fn(),
  };
  vi.mocked(createVillageScene).mockReturnValue(scene);
  const { rerender } = render(
    <Hamlet snapshot={{ ...snapshot, epoch: "one" }} connected />,
  );
  fireEvent.click(screen.getByRole("button", { name: /Select Reader/ }));
  expect(scene.select).toHaveBeenLastCalledWith("#residents/reader");
  expect(
    screen
      .getByRole("button", { name: /Select Reader/ })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  expect(
    screen.getByRole("link", { name: "Full profile →" }).getAttribute("href"),
  ).toBe("#residents/reader");
  rerender(
    <Hamlet
      snapshot={{
        ...snapshot,
        epoch: "one",
        residents: [
          {
            ...resident,
            lifecycle: { state: "archived" },
            unresolved_runs: 2,
          } as Resident,
        ],
      }}
      connected
    />,
  );
  expect(screen.getByRole("dialog", { name: "Reader at home" })).toBeTruthy();
  expect(screen.getAllByText(/Accounting holds remain/).length).toBeGreaterThan(
    0,
  );
  fireEvent.click(screen.getByRole("button", { name: "Select Townhall" }));
  rerender(<Hamlet snapshot={{ ...snapshot, epoch: "two" }} connected />);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("pauses a retained scene while records are open and restores keyboard focus on Escape", () => {
  const scene = {
    active: vi.fn(),
    update: vi.fn(),
    select: vi.fn(),
    dispose: vi.fn(),
    zoom: vi.fn(),
    rotate: vi.fn(),
    overview: vi.fn(),
  };
  vi.mocked(createVillageScene).mockReturnValue(scene);
  const { rerender } = render(<Hamlet snapshot={snapshot} connected />);
  const button = screen.getByRole("button", { name: /Select Reader/ });
  button.focus();
  fireEvent.click(button);
  expect(document.activeElement).toBe(screen.getByRole("dialog"));
  rerender(<Hamlet snapshot={snapshot} connected active={false} />);
  expect(scene.active).toHaveBeenLastCalledWith(false);
  rerender(<Hamlet snapshot={snapshot} connected />);
  expect(createVillageScene).toHaveBeenCalledTimes(1);
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(document.activeElement).toBe(button);
  expect(scene.select).toHaveBeenLastCalledWith(null);
});
it("keeps the initial opener through mesh selection and Escape from camera controls", () => {
  const scene = {
    active: vi.fn(),
    update: vi.fn(),
    select: vi.fn(),
    dispose: vi.fn(),
    zoom: vi.fn(),
    rotate: vi.fn(),
    overview: vi.fn(),
  };
  vi.mocked(createVillageScene).mockReturnValue(scene);
  render(<Hamlet snapshot={snapshot} connected />);
  const opener = screen.getByRole("button", { name: /Select Reader/ });
  opener.focus();
  fireEvent.click(opener);
  const onSelect = vi.mocked(createVillageScene).mock.calls[0][2]!;
  act(() => onSelect("#townhall"));
  const zoom = screen.getByRole("button", { name: "Zoom in" });
  zoom.focus();
  fireEvent.keyDown(zoom, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(opener);
});
