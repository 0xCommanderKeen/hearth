import { expect, it } from "vitest";
import { selectionGesture } from "./gesture";
const press = { pointerId: 1, clientX: 30, clientY: 30, button: 0 };
it("selects only a primary press/release with no intervening navigation", () => {
  const gesture = selectionGesture();
  expect(gesture.up(press)).toBe(false);
  gesture.down(press);
  expect(gesture.up(press)).toBe(true);
  gesture.down({ ...press, button: 2 });
  expect(gesture.up({ ...press, button: 2 })).toBe(false);
});
it("a drag returning to its starting point never selects", () => {
  const gesture = selectionGesture();
  gesture.down(press);
  gesture.move({ ...press, clientX: 70 });
  gesture.move(press);
  expect(gesture.up(press)).toBe(false);
});
it("cancel, pinch and unmatched releases never select", () => {
  const gesture = selectionGesture();
  gesture.down(press);
  gesture.cancel();
  expect(gesture.up(press)).toBe(false);
  gesture.down(press);
  gesture.down({ ...press, pointerId: 2 });
  expect(gesture.up({ ...press, pointerId: 2 })).toBe(false);
  expect(gesture.up(press)).toBe(false);
  gesture.down(press);
  expect(gesture.up({ ...press, pointerId: 2 })).toBe(false);
});
