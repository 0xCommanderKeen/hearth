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
import { createRoomScene } from "./village/room";
vi.mock("./village/room", () => ({ createRoomScene: vi.fn() }));
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

it("keeps the exterior paused and selection intact through rooms, record visits and updates", () => {
  const exterior = {
    active: vi.fn(),
    update: vi.fn(),
    select: vi.fn(),
    dispose: vi.fn(),
    zoom: vi.fn(),
    rotate: vi.fn(),
    overview: vi.fn(),
  };
  vi.mocked(createVillageScene).mockReturnValue(exterior);
  const room = { active: vi.fn(), dispose: vi.fn() };
  vi.mocked(createRoomScene).mockReturnValue(room);
  const { rerender } = render(<Hamlet snapshot={snapshot} connected />);
  fireEvent.click(screen.getByRole("button", { name: /Select Reader/ }));
  fireEvent.click(screen.getByRole("button", { name: /Enter home/ }));
  expect(exterior.active).toHaveBeenLastCalledWith(false);
  expect(screen.getByRole("heading", { name: "Reader’s home" })).toBe(
    document.activeElement,
  );
  expect(
    screen
      .getByRole("link", { name: "Desk · recorded work" })
      .getAttribute("href"),
  ).toBe("#residents/reader?panel=work");
  expect(
    screen
      .getByRole("link", { name: "Journal shelf · journal" })
      .getAttribute("href"),
  ).toBe("#residents/reader?panel=journal");
  expect(
    screen
      .getByRole("link", { name: "Letter cabinet · letters" })
      .getAttribute("href"),
  ).toBe("#residents/reader?panel=letters");
  rerender(<Hamlet snapshot={snapshot} connected active={false} />);
  expect(room.active).toHaveBeenLastCalledWith(false);
  rerender(
    <Hamlet
      snapshot={{
        ...snapshot,
        residents: [{ ...resident, name: "Renamed", presence: "interrupted" }],
      }}
      connected={false}
    />,
  );
  expect(createRoomScene).toHaveBeenCalledTimes(1);
  expect(
    screen.getByText(/Disconnected · showing last known records/).textContent,
  ).toContain("Outcome unknown");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(room.dispose).toHaveBeenCalledOnce();
  expect(exterior.active).toHaveBeenLastCalledWith(true);
  expect(exterior.select).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("dialog", { name: "Renamed at home" })).toBe(
    document.activeElement,
  );
});

it("closes an archived or missing room with history and exit, never substitutes another resident", () => {
  const room = { active: vi.fn(), dispose: vi.fn() };
  vi.mocked(createRoomScene).mockReturnValue(room);
  const { rerender } = render(<Hamlet snapshot={snapshot} connected />);
  fireEvent.click(screen.getByRole("button", { name: /Select Reader/ }));
  fireEvent.click(screen.getByRole("button", { name: /Enter home/ }));
  rerender(
    <Hamlet
      snapshot={{
        ...snapshot,
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
  expect(room.dispose).toHaveBeenCalledOnce();
  expect(screen.getByText(/Their room is closed/)).toBeTruthy();
  expect(screen.getAllByText(/2 unresolved run/)).toHaveLength(2);
  expect(
    screen
      .getByRole("link", { name: /Open resident history/ })
      .getAttribute("href"),
  ).toBe("#residents/reader");
  rerender(
    <Hamlet
      snapshot={{ ...snapshot, residents: [{ ...resident, id: "other" }] }}
      connected
    />,
  );
  expect(
    screen.getByText(/No other resident has taken their place/),
  ).toBeTruthy();
  expect(
    screen
      .getByRole("link", { name: /Open resident history/ })
      .getAttribute("href"),
  ).toBe("#residents-archived");
  fireEvent.click(screen.getByRole("button", { name: "Back to village" }));
  expect(
    screen.getByRole("dialog", { name: "Unavailable resident at home" }),
  ).toBeTruthy();
});

it.each(["initial", "context loss"])(
  "keeps Townhall records and return usable after %s graphics failure",
  (failure) => {
    if (failure === "initial")
      vi.mocked(createRoomScene).mockImplementation(() => {
        throw Error("no WebGL");
      });
    else
      vi.mocked(createRoomScene).mockReturnValue({
        active: vi.fn(),
        dispose: vi.fn(),
      });
    render(<Hamlet snapshot={snapshot} connected />);
    fireEvent.click(screen.getByRole("button", { name: "Select Townhall" }));
    fireEvent.click(screen.getByRole("button", { name: /Enter Townhall/ }));
    if (failure === "context loss")
      act(() => vi.mocked(createRoomScene).mock.calls[0][3]());
    expect(screen.getByText(/Room graphics are unavailable/)).toBeTruthy();
    expect(
      screen.getByRole("link", { name: /Work table/ }).getAttribute("href"),
    ).toBe("#tasks");
    expect(
      screen.getByRole("link", { name: /Ledger shelf/ }).getAttribute("href"),
    ).toBe("#townhall");
    expect(
      screen.getByRole("link", { name: /Letter cabinet/ }).getAttribute("href"),
    ).toBe("#inbox");
    fireEvent.click(screen.getByRole("button", { name: "Back to village" }));
    expect(
      screen.getByRole("dialog", { name: "Townhall household" }),
    ).toBeTruthy();
  },
);
