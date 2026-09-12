import * as THREE from "three";
import { createArtKit, visualIdentity } from "./art.js";
import { selectionGesture } from "./gesture";

export type RoomScene = {
  active(visible: boolean): void;
  occupancy?(atHome: boolean): void;
  lighter(enabled: boolean): void;
  dispose(): void;
};
type RoomTarget = { label: string; href: string };
export type RoomTargets = Record<"work" | "shelf" | "letters", RoomTarget>;

/** One cutaway owns its resources; no exterior geometry or camera is borrowed. */
export function createRoomScene(
  element: HTMLElement,
  townhall: boolean,
  targets: RoomTargets,
  unavailable: () => void,
  residentId?: string,
): RoomScene {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const canvas = renderer.domElement;
  canvas.setAttribute("aria-hidden", "true");
  element.appendChild(canvas);
  const scene = new THREE.Scene();
  const kit = createArtKit();
  const personal = visualIdentity(residentId ?? "townhall");
  const person = residentId ? kit.agent({ id: residentId }) : null;
  if (person) {
    person.scale.multiplyScalar(2.2);
    person.position.set(-0.2, 0.15, 1.7);
    person.visible = false;
    scene.add(person);
  }
  scene.background = new THREE.Color("#cbd5c1");
  scene.add(new THREE.HemisphereLight("#fff5df", "#748267", 2.8));
  const sun = new THREE.DirectionalLight("#fff0cd", 3);
  sun.position.set(2, 9, 6);
  scene.add(sun);
  const camera = new THREE.OrthographicCamera(-6, 6, 5, -5, 0.1, 100);
  camera.position.set(9, 11, 13);
  camera.lookAt(0, 0.8, 0);
  const geometry = new THREE.BoxGeometry(1, 1, 1);
  const materials = new Map<string, THREE.MeshStandardMaterial>();
  const picks: THREE.Object3D[] = [];
  function box(
    color: string,
    position: number[],
    scale: number[],
    target?: keyof RoomTargets,
  ) {
    if (!materials.has(color))
      materials.set(
        color,
        new THREE.MeshStandardMaterial({ color, roughness: 0.9 }),
      );
    const mesh = new THREE.Mesh(geometry, materials.get(color)!);
    mesh.position.set(position[0], position[1], position[2]);
    mesh.scale.set(scale[0], scale[1], scale[2]);
    if (target !== undefined) {
      mesh.userData.href = targets[target].href;
      mesh.userData.label = targets[target].label;
      picks.push(mesh);
    }
    scene.add(mesh);
    return mesh;
  }
  const wood = "#695449",
    cream = "#eee1c4",
    teal = townhall ? "#477a77" : personal.accent,
    paper = "#f8ebcd";
  // Open south/east walls make the furniture readable from the village's angle.
  box("#a17b59", [0, -0.15, 0], [8.4, 0.4, 6.6]);
  for (let i = 0; i < 11; i++)
    box(
      i % 2 ? "#d0ad7c" : "#c5a174",
      [-3.75 + i * 0.75, 0.07, 0],
      [0.72, 0.06, 6.2],
    );
  box(cream, [0, 1.6, -3.1], [8.4, 3.2, 0.2]);
  box(cream, [-4.1, 1.6, 0], [0.2, 3.2, 6.4]);
  box(wood, [0, 3.2, -3.1], [8.5, 0.15, 0.3]);
  box(wood, [-4.1, 3.2, 0], [0.3, 0.15, 6.5]);
  for (const x of [-4, 0, 4]) box(wood, [x, 1.6, -2.95], [0.15, 3.2, 0.15]);
  box(teal, [-4, 1.9, -0.5], [0.08, 1.5, 1.8]);
  box("#ffd788", [-3.94, 1.9, -0.5], [0.07, 1.2, 1.5]);
  box(paper, [-3.88, 1.9, -0.5], [0.08, 1.25, 0.06]);
  box(paper, [-3.88, 1.9, -0.5], [0.08, 0.06, 1.5]);
  box(townhall ? "#3c575f" : personal.accent, [0, 0.12, 0.8], [4.5, 0.04, 3.4]);
  box("#d4ad66", [0, 0.15, 0.8], [4.15, 0.025, 3.05]);
  box(
    townhall ? "#3c575f" : personal.accent,
    [0, 0.17, 0.8],
    [3.95, 0.025, 2.85],
  );
  if (residentId) {
    if (personal.detail === 0) {
      const garden = kit.garden();
      garden.scale.setScalar(1.7);
      garden.position.set(-3.3, 0.1, 1.7);
      scene.add(garden);
    } else if (personal.detail === 1) {
      for (let i = 0; i < 3; i++)
        box(
          i % 2 ? teal : paper,
          [-3.3, 0.2 + i * 0.18, 1.7],
          [0.7, 0.15, 0.5],
        );
    } else if (personal.detail === 2) {
      box(wood, [-3.3, 0.8, 1.7], [0.12, 1.5, 0.12]);
      box("#ffd788", [-3.3, 1.35, 1.7], [0.35, 0.5, 0.35]);
    } else {
      for (let i = 0; i < 3; i++)
        box(wood, [-3.3, 0.22 + i * 0.2, 1.7], [0.8, 0.18, 0.24]);
    }
  }
  // Desk / council table: all parts are the same recorded-work target.
  const dx = townhall ? 0 : -1.7,
    dz = townhall ? 0.5 : -0.9;
  box(wood, [dx, 1.1, dz], [townhall ? 3.1 : 2.5, 0.22, 1.35], "work");
  for (const x of [-0.9, 0.9])
    for (const z of [-0.45, 0.45])
      box(wood, [dx + x, 0.55, dz + z], [0.16, 1, 0.16], "work");
  box(paper, [dx, 1.24, dz], [0.95, 0.05, 0.65], "work");
  for (let i = 0; i < 4; i++)
    box(teal, [dx, 1.27, dz - 0.2 + i * 0.12], [0.65, 0.01, 0.025], "work");
  box(teal, [dx, 0.55, dz + 1.1], [0.8, 0.18, 0.75], "work");
  box(teal, [dx, 0.95, dz + 1.4], [0.8, 0.9, 0.15], "work");
  for (const x of [-0.3, 0.3])
    box(wood, [dx + x, 0.3, dz + 1.1], [0.12, 0.5, 0.6], "work");
  // Journal / household ledger shelf.
  box(wood, [1.1, 1.2, -2.65], [1.7, 2.4, 0.6], "shelf");
  for (let row = 0; row < 3; row++) {
    box(cream, [1.1, 0.4 + row * 0.7, -2.29], [1.5, 0.08, 0.65], "shelf");
    for (let i = 0; i < 5; i++)
      box(
        [teal, "#be6549", "#d4ad66"][i % 3],
        [0.5 + i * 0.28, 0.65 + row * 0.7, -2.4],
        [0.18, 0.45, 0.45],
        "shelf",
      );
  }
  // A separate letter cabinet and a visible envelope.
  box(teal, [2.8, 0.7, 0.8], [1.25, 1.4, 0.95], "letters");
  for (const y of [0.35, 0.8, 1.2]) {
    box(cream, [2.8, y, 1.29], [1.05, 0.025, 0.025], "letters");
    box("#d4ad66", [2.8, y + 0.13, 1.32], [0.2, 0.07, 0.07], "letters");
  }
  box(paper, [2.8, 1.43, 0.8], [0.7, 0.06, 0.5], "letters");
  box("#be6549", [2.8, 1.47, 0.8], [0.13, 0.02, 0.13], "letters");
  if (!townhall) {
    box(wood, [-2.5, 0.35, 2.05], [2.1, 0.5, 1.35]);
    box(paper, [-2.5, 0.65, 2.05], [2, 0.24, 1.25]);
    box(teal, [-2.2, 0.8, 2.05], [1.3, 0.08, 1.26]);
    box(paper, [-3.15, 0.84, 2.05], [0.45, 0.18, 0.9]);
  } else {
    box(teal, [-2.2, 2, -2.94], [2.3, 1.35, 0.12]);
    for (const x of [-2.8, -2.2, -1.6])
      box(paper, [x, 2, -2.85], [0.45, 0.8, 0.05]);
  }
  let disposed = false,
    visible = true;
  function draw() {
    if (disposed || !visible || document.hidden) return;
    const width = element.clientWidth,
      height = element.clientHeight;
    if (!width || !height) return;
    const aspect = width / height;
    const half = Math.max(5.3, 6.4 / aspect);
    Object.assign(camera, {
      left: -half * aspect,
      right: half * aspect,
      top: half,
      bottom: -half,
    });
    camera.updateProjectionMatrix();
    renderer.setSize(width, height);
    renderer.render(scene, camera);
  }
  const observer = new ResizeObserver(draw);
  observer.observe(element);
  const gesture = selectionGesture();
  const ray = new THREE.Raycaster();
  function pick(event: PointerEvent) {
    const bounds = canvas.getBoundingClientRect();
    ray.setFromCamera(
      new THREE.Vector2(
        ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
        (-(event.clientY - bounds.top) / bounds.height) * 2 + 1,
      ),
      camera,
    );
    return ray.intersectObjects(picks, false)[0]?.object;
  }
  const down = (event: PointerEvent) => gesture.down(event);
  const move = (event: PointerEvent) => {
    gesture.move(event);
    const hit = pick(event);
    canvas.style.cursor = hit ? "pointer" : "default";
    canvas.title =
      hit?.userData.label ?? "Select the desk, shelf or letter cabinet";
  };
  const up = (event: PointerEvent) => {
    if (!gesture.up(event)) return;
    const hit = pick(event);
    if (hit) window.location.hash = hit.userData.href;
  };
  const cancel = () => gesture.cancel();
  function dispose() {
    if (disposed) return;
    disposed = true;
    observer.disconnect();
    document.removeEventListener("visibilitychange", draw);
    canvas.removeEventListener("pointerdown", down);
    canvas.removeEventListener("pointermove", move);
    canvas.removeEventListener("pointerup", up);
    canvas.removeEventListener("pointercancel", cancel);
    canvas.removeEventListener("webglcontextlost", lost);
    kit.dispose();
    geometry.dispose();
    materials.forEach((material) => material.dispose());
    renderer.dispose();
    if (!renderer.getContext().isContextLost()) renderer.forceContextLoss();
    canvas.remove();
  }
  function lost(event: Event) {
    event.preventDefault();
    dispose();
    unavailable();
  }
  canvas.addEventListener("pointerdown", down);
  canvas.addEventListener("pointermove", move);
  canvas.addEventListener("pointerup", up);
  canvas.addEventListener("pointercancel", cancel);
  canvas.addEventListener("webglcontextlost", lost);
  document.addEventListener("visibilitychange", draw);
  draw();
  return {
    occupancy(atHome) {
      if (person) {
        person.visible = atHome;
        draw();
      }
    },
    lighter(enabled) {
      if (disposed) return;
      renderer.setPixelRatio(
        Math.min(window.devicePixelRatio, enabled ? 1 : 2),
      );
      draw();
    },
    active(value) {
      visible = value;
      draw();
    },
    dispose,
  };
}
