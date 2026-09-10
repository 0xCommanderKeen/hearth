import { expect, it } from "vitest";
import { Box3, OrthographicCamera, Vector3 } from "three";
import { createCameraController } from "./camera";

function setup() {
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0.1, 10000);
  const controls = {
    target: new Vector3(),
    update() {
      camera.lookAt(this.target);
      camera.updateMatrixWorld(true);
    },
    addEventListener() {},
  };
  return { camera, controls, view: createCameraController(camera, controls) };
}
for (const count of [0, 4, 24, 100]) {
  for (const ratio of [0.55, 1.9]) {
    it(`fits all corners for ${count} residents at aspect ${ratio}`, () => {
      const { camera, view } = setup();
      const bounds = new Box3(
        new Vector3(-14, -1, -Math.max(10, count * 1.25)),
        new Vector3(14, 5, 10),
      );
      view.bounds(bounds);
      view.resize(ratio);
      for (const x of [bounds.min.x, bounds.max.x])
        for (const y of [bounds.min.y, bounds.max.y])
          for (const z of [bounds.min.z, bounds.max.z]) {
            const point = new Vector3(x, y, z).project(camera);
            expect(Math.abs(point.x)).toBeLessThan(0.9);
            expect(Math.abs(point.y)).toBeLessThan(0.9);
            expect(Math.abs(point.z)).toBeLessThan(1);
          }
      // Angled, not an overhead plan: both height and ground direction remain visible.
      const direction = camera.getWorldDirection(new Vector3());
      expect(Math.abs(direction.y)).toBeGreaterThan(0.7);
      expect(Math.abs(direction.y)).toBeLessThan(0.9);
    });
  }
}
it("keeps custom camera across roster/resize updates and overview recovers all bounds", () => {
  const { camera, controls, view } = setup();
  const bounds = new Box3(new Vector3(-14, -1, -10), new Vector3(14, 5, 10));
  view.bounds(bounds);
  view.zoom(2);
  view.rotate(1);
  const position = camera.position.clone();
  const target = controls.target.clone();
  view.bounds(new Box3(new Vector3(-14, -1, -40), new Vector3(14, 5, 10)));
  view.resize(0.5);
  expect(camera.position).toEqual(position);
  expect(controls.target).toEqual(target);
  expect(camera.zoom).toBe(2);
  view.overview();
  expect(camera.zoom).toBe(1);
  expect(controls.target.z).toBe(-15);
});
it("bounds zoom and returns a removed-row pan to the remaining settlement", () => {
  const { camera, controls, view } = setup();
  const bounds = new Box3(new Vector3(-14, -1, -10), new Vector3(14, 5, 10));
  view.bounds(bounds);
  view.zoom(100);
  expect(camera.zoom).toBe(5);
  view.zoom(0.0001);
  expect(camera.zoom).toBe(0.5);
  const offset = camera.position.clone().sub(controls.target);
  controls.target.z = -100;
  camera.position.copy(controls.target).add(offset);
  view.bounds(bounds);
  expect(controls.target.z).toBe(-10);
  expect(
    camera.position.clone().sub(controls.target).distanceTo(offset),
  ).toBeLessThan(1e-10);
});
it("restores the exact pre-selection camera after focusing different homes", () => {
  const { camera, controls, view } = setup();
  view.bounds(new Box3(new Vector3(-20, -1, -20), new Vector3(20, 5, 20)));
  view.zoom(1.25);
  view.rotate(1);
  const position = camera.position.clone(),
    target = controls.target.clone();
  const restore = view.capture();
  view.focus(new Vector3(6, 0, 6));
  expect(controls.target).toEqual(new Vector3(6, 0, 6));
  view.focus(new Vector3(-6, 0, -6));
  view.resize(0.5);
  restore();
  expect(camera.position).toEqual(position);
  expect(controls.target).toEqual(target);
  expect(camera.zoom).toBe(1.25);
});

it.each([20, 40])(
  "refits a restored overview to the current narrow aspect and bounds %s",
  (extent) => {
    const { camera, controls, view } = setup();
    view.bounds(new Box3(new Vector3(-20, -1, -20), new Vector3(20, 5, 20)));
    view.resize(1.9);
    const restore = view.capture();
    view.focus(new Vector3(6, 0, 6));
    view.resize(0.5);
    const current = new Box3(
      new Vector3(-extent, -1, -20),
      new Vector3(20, 5, extent),
    );
    view.bounds(current);
    restore();
    expect(controls.target).toEqual(current.getCenter(new Vector3()));
    for (const x of [current.min.x, current.max.x])
      for (const y of [current.min.y, current.max.y])
        for (const z of [current.min.z, current.max.z]) {
          const point = new Vector3(x, y, z).project(camera);
          expect(Math.abs(point.x)).toBeLessThan(0.9);
          expect(Math.abs(point.y)).toBeLessThan(0.9);
          expect(Math.abs(point.z)).toBeLessThan(1);
        }
    expect(camera.zoom).toBe(1);
  },
);
it("returns a saved custom target to the shrunken village without changing its orientation or zoom", () => {
  const { camera, controls, view } = setup();
  view.bounds(new Box3(new Vector3(-60, -1, -60), new Vector3(60, 5, 60)));
  view.zoom(1.8);
  view.rotate(1);
  view.focus(new Vector3(50, 0, 50));
  const offset = camera.position.clone().sub(controls.target);
  const direction = camera.getWorldDirection(new Vector3());
  const restore = view.capture();
  view.focus(new Vector3(6, 0, 6));
  view.bounds(new Box3(new Vector3(-10, -1, -10), new Vector3(10, 5, 10)));
  view.resize(0.5);
  restore();
  expect(controls.target).toEqual(new Vector3(10, 0, 10));
  expect(
    camera.position.clone().sub(controls.target).distanceTo(offset),
  ).toBeLessThan(1e-10);
  expect(
    camera.getWorldDirection(new Vector3()).distanceTo(direction),
  ).toBeLessThan(1e-10);
  expect(camera.zoom).toBe(1.8);
});

it("restores an intentional outside pan exactly when the settlement has not shrunk", () => {
  const { camera, controls, view } = setup();
  const bounds = new Box3(new Vector3(-20, -1, -20), new Vector3(20, 5, 20));
  view.bounds(bounds);
  view.zoom(1.25);
  view.rotate(1);
  view.focus(new Vector3(100, 0, -100));
  const position = camera.position.clone(),
    target = controls.target.clone();
  const restore = view.capture();
  view.focus(new Vector3(6, 0, 6));
  view.bounds(bounds.clone());
  view.resize(0.5);
  restore();
  expect(camera.position).toEqual(position);
  expect(controls.target).toEqual(target);
  expect(camera.zoom).toBe(1.25);
});
