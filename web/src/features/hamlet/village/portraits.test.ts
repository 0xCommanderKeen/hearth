// @vitest-environment jsdom
import { expect, it, vi } from "vitest";
const graphics = vi.hoisted(() => ({
  renders: 0,
  contexts: 0,
  disposed: 0,
  lost: 0,
}));
vi.mock("three", async (original) => ({
  ...(await original<typeof import("three")>()),
  WebGLRenderer: class {
    constructor() {
      graphics.contexts++;
    }
    setSize() {}
    render() {
      graphics.renders++;
    }
    domElement = { toDataURL: () => `portrait-${graphics.renders}` };
    dispose() {
      graphics.disposed++;
    }
    forceContextLoss() {
      graphics.lost++;
    }
  },
}));
import { requestPortrait } from "./portraits";
it("batches duplicate identities, cancels unmounted work and releases the context", () => {
  let frame = () => {};
  vi.stubGlobal("requestAnimationFrame", (fn: () => void) => {
    frame = fn;
    return 1;
  });
  const first = vi.fn(),
    duplicate = vi.fn(),
    cancelled = vi.fn();
  requestPortrait("same", first);
  requestPortrait("same", duplicate);
  requestPortrait("cancelled", cancelled)();
  frame();
  expect(graphics.renders).toBe(1);
  expect(first).toHaveBeenCalledWith("portrait-1");
  expect(duplicate).toHaveBeenCalledWith("portrait-1");
  expect(cancelled).not.toHaveBeenCalled();
  expect(graphics.disposed).toBe(graphics.contexts);
  expect(graphics.lost).toBe(graphics.contexts);
  const cached = vi.fn();
  requestPortrait("same", cached);
  expect(cached).toHaveBeenCalledWith("portrait-1");
  expect(graphics.renders).toBe(1);
  // The cache retains at most 128 identities; an evicted identity renders again.
  for (let i = 0; i < 129; i++) requestPortrait(`resident-${i}`, vi.fn());
  frame();
  const before = graphics.renders;
  requestPortrait("same", vi.fn());
  frame();
  expect(graphics.renders).toBe(before + 1);
  expect(graphics.disposed).toBe(graphics.contexts);
  vi.unstubAllGlobals();
});
