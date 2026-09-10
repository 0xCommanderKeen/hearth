import { Box3, OrthographicCamera, Vector3 } from "three";
type Controls = {
  target: Vector3;
  update(): unknown;
  addEventListener(type: "start", listener: () => void): void;
};
const ANGLE = Math.PI / 5;
export const ZOOM_LIMITS = { min: 0.5, max: 5 };
/** Fit model bounds in screen space, with margin and visible facades. */
export function createCameraController(
  camera: OrthographicCamera,
  controls: Controls,
) {
  let settlement = new Box3();
  let aspect = 1;
  let height = 1;
  let atOverview = true;
  controls.addEventListener("start", () => {
    atOverview = false;
  });
  function projection() {
    camera.left = (-height * aspect) / 2;
    camera.right = (height * aspect) / 2;
    camera.top = height / 2;
    camera.bottom = -height / 2;
    camera.updateProjectionMatrix();
  }
  function overview() {
    if (settlement.isEmpty()) return;
    atOverview = true;
    const center = settlement.getCenter(new Vector3());
    const distance = settlement.getSize(new Vector3()).length() * 2;
    controls.target.copy(center);
    camera.position
      .copy(center)
      .add(
        new Vector3(Math.sin(ANGLE), 1.35, Math.cos(ANGLE))
          .normalize()
          .multiplyScalar(distance),
      );
    camera.zoom = 1;
    camera.lookAt(center);
    camera.updateMatrixWorld(true);
    const projected = new Box3();
    for (const x of [settlement.min.x, settlement.max.x])
      for (const y of [settlement.min.y, settlement.max.y])
        for (const z of [settlement.min.z, settlement.max.z])
          projected.expandByPoint(
            new Vector3(x, y, z).applyMatrix4(camera.matrixWorldInverse),
          );
    const size = projected.getSize(new Vector3());
    height = Math.max(size.y, size.x / aspect) * 1.12;
    projection();
    controls.update();
  }
  function clampTarget() {
    // Keep the viewing direction while returning a removed-row pan to the village.
    const target = controls.target.clone();
    controls.target.clamp(settlement.min, settlement.max);
    camera.position.add(controls.target.clone().sub(target));
    controls.update();
  }
  return {
    bounds(bounds: Box3) {
      settlement = bounds.clone();
      if (atOverview) overview();
      else clampTarget();
    },
    resize(ratio: number) {
      aspect = ratio;
      if (atOverview) overview();
      else projection();
    },
    capture() {
      const saved = {
        position: camera.position.clone(),
        target: controls.target.clone(),
        zoom: camera.zoom,
        height,
        atOverview,
        bounds: settlement.clone(),
      };
      return () => {
        if (saved.atOverview) {
          // The available viewport and village may have changed while inspecting.
          overview();
          return;
        }
        camera.position.copy(saved.position);
        controls.target.copy(saved.target);
        camera.zoom = saved.zoom;
        height = saved.height;
        atOverview = false;
        projection();
        if (settlement.containsBox(saved.bounds)) controls.update();
        else clampTarget();
      };
    },
    focus(target: Vector3) {
      atOverview = false;
      camera.position.add(target.clone().sub(controls.target));
      controls.target.copy(target);
      controls.update();
    },
    overview,
    zoom(factor: number) {
      atOverview = false;
      camera.zoom = Math.min(
        ZOOM_LIMITS.max,
        Math.max(ZOOM_LIMITS.min, camera.zoom * factor),
      );
      camera.updateProjectionMatrix();
      controls.update();
    },
    rotate(direction: number) {
      atOverview = false;
      const offset = camera.position.clone().sub(controls.target);
      offset.applyAxisAngle(new Vector3(0, 1, 0), (direction * Math.PI) / 8);
      camera.position.copy(controls.target).add(offset);
      controls.update();
    },
  };
}
