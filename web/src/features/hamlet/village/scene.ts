import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { createArtKit } from "./art.js";
import { createCameraController, ZOOM_LIMITS } from "./camera";
import { selectionGesture } from "./gesture";
import type { LetterEvent, Resident } from "../../../shared/client";

const WALK_MS = 4200;
const WALKS_AT_ONCE = 3;
export type VillageScene = {
  update(residents: Resident[], letters: LetterEvent[]): void;
  overview(): void;
  zoom(factor: number): void;
  rotate(direction: number): void;
  dispose(): void;
};

// Owns GPU resources, controls and observations for one mounted village. Record updates
// replace only models when the roster changes; neither React nor snapshots own the camera.
export function createVillageScene(
  element: HTMLElement,
  unavailable: () => void,
): VillageScene {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  element.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color("#cbd5c1");
  scene.add(new THREE.HemisphereLight("#fff5df", "#748267", 2.4));
  const sun = new THREE.DirectionalLight("#fff0cd", 3);
  sun.position.set(-8, 16, 8);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, {
    left: -15,
    right: 15,
    top: 15,
    bottom: -15,
  });
  scene.add(sun);

  const kit = createArtKit();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = false;
  controls.minPolarAngle = Math.PI / 6;
  controls.maxPolarAngle = Math.PI / 3;
  controls.minZoom = ZOOM_LIMITS.min;
  controls.maxZoom = ZOOM_LIMITS.max;
  controls.screenSpacePanning = false;
  controls.touches.ONE = THREE.TOUCH.PAN;
  controls.touches.TWO = THREE.TOUCH.DOLLY_ROTATE;
  const view = createCameraController(camera, controls);
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let village = new THREE.Group();
  let targets: THREE.Object3D[] = [];
  const doors = new Map<string, THREE.Vector3>();
  let letters: LetterEvent[] = [];
  const walked = new Set<string>();
  let identities: string | undefined;
  let releaseModels = () => {};
  function rebuild(residents: Resident[]) {
    walks.forEach((walk) => scene.remove(walk.person));
    walks.length = 0;
    scene.remove(village);
    releaseModels();
    village = new THREE.Group();
    const rows = Math.max(1, Math.ceil((residents.length + 1) / 4));
    const depth = Math.max(19, rows * 5 + 10);
    const centerZ = -(rows - 1) * 2.5;
    const groundGeometry = new THREE.BoxGeometry(26, 0.5, depth);
    const groundMaterial = new THREE.MeshStandardMaterial({
      color: "#98ad83",
      roughness: 1,
    });
    const ground = new THREE.Mesh(groundGeometry, groundMaterial);
    ground.position.set(0, -0.3, centerZ);
    ground.receiveShadow = true;
    village.add(ground);
    const pathGeometry = new THREE.BoxGeometry(19, 0.04, 1.5);
    const pathMaterial = new THREE.MeshStandardMaterial({ color: "#dbc8a3" });
    const path = new THREE.Mesh(pathGeometry, pathMaterial);
    path.position.z = 1.8;
    path.receiveShadow = true;
    village.add(path);
    targets = [];
    function building(
      id: string,
      kind: string,
      x: number,
      z: number,
      href: string,
    ) {
      const object = kit.building({ id, kind, width: 3.5, depth: 3.3 });
      object.position.set(x, 0, z);
      object.userData.href = href;
      targets.push(object);
      village.add(object);
    }
    // Where a letter is handed over. The operator has no home in the village, so a
    // letter it wrote leaves from Townhall — the one door it actually stands at.
    doors.clear();
    building("townhall", "lodge", 7.5, -2, "#townhall");
    doors.set("operator", new THREE.Vector3(7.5, 0, -2 + 2.2));
    const square = kit.building({
      id: "square",
      kind: "square",
      width: 3,
      depth: 3,
    });
    square.position.set(0, 0, 4);
    village.add(square);
    residents.forEach((r, i) => {
      // The first row reserves its last plot for Townhall.
      const slot = i < 3 ? i : i + 1;
      const x = -7.5 + (slot % 4) * 5;
      const z = -2 - Math.floor(slot / 4) * 5;
      building(r.id, "home", x, z, `#residents/${encodeURIComponent(r.id)}`);
      const person = kit.agent({ id: r.id });
      person.position.set(x, 0, z + 2.2);
      person.userData.href = `#residents/${encodeURIComponent(r.id)}`;
      targets.push(person);
      village.add(person);
      doors.set(r.id, new THREE.Vector3(x, 0, z + 2.2));
    });
    [
      [-11, -6],
      [-11, 5],
      [-7, 7],
      [8, 6],
      [11, -5],
      [11, centerZ * 2 - 6],
      [-11, centerZ * 2 - 6],
    ].forEach(([x, z], i) => {
      const tree = kit.tree(i);
      tree.position.set(x, 0, z);
      village.add(tree);
    });

    scene.add(village);
    view.bounds(new THREE.Box3().setFromObject(village));
    releaseModels = () => {
      groundGeometry.dispose();
      groundMaterial.dispose();
      pathGeometry.dispose();
      pathMaterial.dispose();
    };
  }
  const ray = new THREE.Raycaster();
  const gesture = selectionGesture();
  const pointerDown = (e: PointerEvent) => gesture.down(e);
  const pointerMove = (e: PointerEvent) => gesture.move(e);
  const pointerCancel = () => gesture.cancel();
  const pick = (e: PointerEvent) => {
    if (!gesture.up(e)) return;
    const rect = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(
      new THREE.Vector2(
        ((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1,
      ),
      camera,
    );
    let hit: THREE.Object3D | null =
      ray.intersectObjects(targets, true)[0]?.object ?? null;
    while (hit && !hit.userData.href) hit = hit.parent;
    if (hit) window.location.hash = hit.userData.href;
  };
  const canvas = renderer.domElement;
  canvas.addEventListener("pointerdown", pointerDown);
  canvas.addEventListener("pointermove", pointerMove);
  canvas.addEventListener("pointercancel", pointerCancel);
  canvas.addEventListener("pointerup", pick);
  const resize = new ResizeObserver(() => {
    const width = Math.max(1, element.clientWidth);
    const height = Math.max(1, element.clientHeight);
    view.resize(width / height);
    renderer.setSize(width, height);
  });
  resize.observe(element);
  // One walk per letter event that has two doors in this village. Nothing here is
  // scheduled, looped or embellished: the village walks what the snapshot reported and
  // then stands still, so an empty post is an empty village rather than a busy one.
  const walks: {
    person: THREE.Group;
    from: THREE.Vector3;
    to: THREE.Vector3;
    started: number;
  }[] = [];
  function open(at: number) {
    // Oldest first, so a chain is walked in the order it happened.
    for (let index = letters.length - 1; index >= 0; index -= 1) {
      if (walks.length >= WALKS_AT_ONCE) return;
      const event = letters[index];
      const key = `${event.kind}:${event.task_id}`;
      if (walked.has(key)) continue;
      const from = doors.get(event.from_resident_id ?? "operator");
      const to = doors.get(event.to_resident_id ?? "operator");
      walked.add(key);
      // A resident that has left the village has no door to walk to. The letter still
      // happened and is still listed below; it is simply not drawn.
      if (!from || !to || from === to) continue;
      const person = kit.agent({ id: key });
      person.position.copy(from);
      scene.add(person);
      walks.push({ person, from, to, started: at });
    }
  }
  renderer.setAnimationLoop(() => {
    const at = performance.now();
    if (!motion.matches) open(at);
    else {
      walks.forEach((walk) => scene.remove(walk.person));
      walks.length = 0;
      letters.forEach((event) => walked.add(`${event.kind}:${event.task_id}`));
    }
    for (let index = walks.length - 1; index >= 0; index -= 1) {
      const walk = walks[index];
      const travelled = (at - walk.started) / WALK_MS;
      if (travelled >= 1) {
        scene.remove(walk.person);
        walks.splice(index, 1);
        continue;
      }
      walk.person.position.lerpVectors(walk.from, walk.to, travelled);
      walk.person.lookAt(walk.to.x, walk.person.position.y, walk.to.z);
    }
    controls.update();
    renderer.render(scene, camera);
  });

  let disposed = false;
  function dispose() {
    if (disposed) return;
    disposed = true;
    renderer.setAnimationLoop(null);
    resize.disconnect();
    controls.dispose();
    canvas.removeEventListener("pointerdown", pointerDown);
    canvas.removeEventListener("pointermove", pointerMove);
    canvas.removeEventListener("pointercancel", pointerCancel);
    canvas.removeEventListener("pointerup", pick);
    canvas.removeEventListener("webglcontextlost", lost);
    kit.dispose();
    releaseModels();
    sun.shadow.dispose();
    renderer.dispose();
    canvas.remove();
  }
  function lost(event: Event) {
    event.preventDefault();
    dispose();
    unavailable();
  }
  canvas.addEventListener("webglcontextlost", lost);
  return {
    update(residents, post) {
      if (disposed) return;
      letters = post;
      const next = JSON.stringify(residents.map((resident) => resident.id));
      if (next !== identities) {
        rebuild(residents);
        identities = next;
      }
    },
    overview: view.overview,
    zoom: view.zoom,
    rotate: view.rotate,
    dispose,
  };
}
