import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { createArtKit } from "./art.js";
import { createCameraController, ZOOM_LIMITS } from "./camera";
import { createPlotAllocator, type Plot } from "./layout";
import { selectionGesture } from "./gesture";
import type { LetterEvent, Resident } from "../../../shared/client";

const WALK_MS = 4200;
const WALKS_AT_ONCE = 3;
export type VillageScene = {
  update(residents: Resident[], letters: LetterEvent[], epoch: string): void;
  select(id: string | null): void;
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
  onSelect: (id: string) => void = () => {},
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
  let epoch: string | undefined;
  let allocate = createPlotAllocator("");
  let selected: string | null = null;
  let hovered: string | null = null;
  const labels = document.createElement("div");
  labels.className = "scene-labels";
  element.appendChild(labels);
  let names: { id: string; node: HTMLButtonElement; anchor: THREE.Vector3 }[] =
    [];
  let outlines: { id: string; mesh: THREE.Mesh }[] = [];
  function highlight() {
    outlines.forEach(({ id, mesh }) => {
      mesh.visible = id === selected || id === hovered;
    });
    names.forEach(({ id, node }) => {
      node.classList.toggle("selected", id === selected);
      node.classList.toggle("hovered", id === hovered);
      node.setAttribute("aria-pressed", String(id === selected));
    });
  }
  function select(id: string | null) {
    selected = id;
    highlight();
  }
  function choose(id: string) {
    select(id);
    onSelect(id);
  }

  let releaseModels = () => {};
  function rebuild(residents: Resident[]) {
    walks.forEach((walk) => scene.remove(walk.person));
    walks.length = 0;
    scene.remove(village);
    releaseModels();
    village = new THREE.Group();
    const plots = allocate(residents.map((r) => r.id));
    const all: Plot[] = [
      { id: "townhall", x: 0, z: -6 },
      { id: "square", x: 0, z: 0 },
      ...plots,
    ];
    const minX = Math.min(...all.map((p) => p.x)) - 5;
    const maxX = Math.max(...all.map((p) => p.x)) + 5;
    const minZ = Math.min(...all.map((p) => p.z)) - 5;
    const maxZ = Math.max(...all.map((p) => p.z)) + 5;
    const groundGeometry = new THREE.BoxGeometry(maxX - minX, 0.5, maxZ - minZ);
    const groundMaterial = new THREE.MeshStandardMaterial({
      color: "#98ad83",
      roughness: 1,
    });
    const ground = new THREE.Mesh(groundGeometry, groundMaterial);
    ground.position.set((minX + maxX) / 2, -0.3, (minZ + maxZ) / 2);
    ground.receiveShadow = true;
    village.add(ground);
    const pathMaterial = new THREE.MeshStandardMaterial({ color: "#dbc8a3" });
    const paths: THREE.BoxGeometry[] = [];
    function street(x: number, z: number, width: number, depth: number) {
      const geometry = new THREE.BoxGeometry(width, 0.04, depth);
      paths.push(geometry);
      const path = new THREE.Mesh(geometry, pathMaterial);
      path.position.set(x, 0, z);
      path.receiveShadow = true;
      village.add(path);
    }
    // Every front door meets its row street; the north/south lane lies between plots.
    for (const z of new Set(all.map((p) => p.z)))
      street((minX + maxX) / 2, z + 3, maxX - minX - 2, 1.2);
    street(3, (minZ + maxZ) / 2, 1.2, maxZ - minZ - 2);
    const ringGeometry = new THREE.RingGeometry(2.35, 2.55, 48);
    const ringMaterial = new THREE.MeshBasicMaterial({
      color: "#fff3b1",
      side: THREE.DoubleSide,
    });
    names = [];
    outlines = [];
    labels.replaceChildren();
    targets = [];
    function building(
      id: string,
      kind: string,
      x: number,
      z: number,
      href: string,
      name: string,
    ) {
      const object = kit.building({ id, kind, width: 3.5, depth: 3.3 });
      object.position.set(x, 0, z);
      object.userData.href = href;
      object.userData.identity = href;
      const outline = new THREE.Mesh(ringGeometry, ringMaterial);
      outline.rotation.x = -Math.PI / 2;
      outline.position.set(x, 0.05, z);
      village.add(outline);
      outlines.push({ id: href, mesh: outline });
      const node = document.createElement("button");
      node.type = "button";
      node.className = "scene-label";
      node.textContent = name;
      node.title = name;
      node.dataset.identity = href;
      node.setAttribute("aria-label", `Select ${name}`);
      node.onclick = () => choose(href);
      node.onpointerenter = () => {
        hovered = href;
        highlight();
      };
      node.onpointerleave = () => {
        hovered = null;
        highlight();
      };
      labels.appendChild(node);
      names.push({ id: href, node, anchor: new THREE.Vector3(x, 3.6, z) });
      street(x, z + 2.35, 0.8, 1.3);
      targets.push(object);
      village.add(object);
    }
    // Where a letter is handed over. The operator has no home in the village, so a
    // letter it wrote leaves from Townhall — the one door it actually stands at.
    doors.clear();
    building("townhall", "lodge", 0, -6, "#townhall", "Townhall");
    doors.set("operator", new THREE.Vector3(0, 0, -6 + 2.2));
    const square = kit.building({
      id: "square",
      kind: "square",
      width: 3,
      depth: 3,
    });
    square.position.set(0, 0, 0);
    village.add(square);
    const byId = new Map(residents.map((r) => [r.id, r]));
    plots.forEach(({ id, x, z }) => {
      const r = byId.get(id)!;
      building(
        id,
        "home",
        x,
        z,
        `#residents/${encodeURIComponent(id)}`,
        r.name,
      );
      const person = kit.agent({ id });
      person.position.set(x, 0, z + 2.2);
      // People and landscaping are decorative, never raycast selection targets.
      village.add(person);
      doors.set(id, new THREE.Vector3(x, 0, z + 2.2));
    });
    [
      [minX + 1, minZ + 1],
      [maxX - 1, minZ + 1],
      [minX + 1, maxZ - 1],
      [maxX - 1, maxZ - 1],
    ].forEach(([x, z], i) => {
      const tree = kit.tree(i);
      tree.position.set(x, 0, z);
      village.add(tree);
    });
    highlight();

    scene.add(village);
    view.bounds(new THREE.Box3().setFromObject(village));
    releaseModels = () => {
      groundGeometry.dispose();
      groundMaterial.dispose();
      paths.forEach((geometry) => geometry.dispose());
      ringGeometry.dispose();
      ringMaterial.dispose();
      pathMaterial.dispose();
    };
  }
  const ray = new THREE.Raycaster();
  const gesture = selectionGesture();
  const pointerDown = (e: PointerEvent) => gesture.down(e);
  const pointerMove = (e: PointerEvent) => {
    gesture.move(e);
    hovered = hitIdentity(e);
    canvas.style.cursor = hovered ? "pointer" : "grab";
    highlight();
  };
  const pointerLeave = () => {
    hovered = null;
    highlight();
  };
  const pointerCancel = () => gesture.cancel();
  const hitIdentity = (e: PointerEvent): string | null => {
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
    while (hit && !hit.userData.identity) hit = hit.parent;
    return hit?.userData.identity ?? null;
  };
  const pick = (e: PointerEvent) => {
    if (!gesture.up(e)) return;
    const id = hitIdentity(e);
    if (id) choose(id);
  };
  const canvas = renderer.domElement;
  canvas.addEventListener("pointerdown", pointerDown);
  canvas.addEventListener("pointermove", pointerMove);
  canvas.addEventListener("pointerleave", pointerLeave);
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
    const width = element.clientWidth,
      height = element.clientHeight;
    // Hide collisions at overview density. Selected/hovered identities win; every
    // identity remains available in the directory and labels reveal as you zoom.
    const occupied: { x: number; y: number; w: number; h: number }[] = [];
    const ordered = [...names].sort(
      (a, b) =>
        Number(b.id === selected || b.id === hovered) -
        Number(a.id === selected || a.id === hovered),
    );
    for (const { node, anchor } of ordered) {
      const p = anchor.clone().project(camera);
      const w = node.offsetWidth,
        h = node.offsetHeight;
      const x = ((p.x + 1) * width) / 2,
        y = ((1 - p.y) * height) / 2;
      const overlaps = occupied.some(
        (b) =>
          Math.abs(b.x - x) < (b.w + w) / 2 + 4 &&
          Math.abs(b.y - y) < (b.h + h) / 2 + 4,
      );
      const visible =
        Math.abs(p.z) < 1 &&
        x >= w / 2 &&
        x <= width - w / 2 &&
        y >= h / 2 &&
        y <= height - h / 2 &&
        !overlaps;
      node.style.visibility = visible ? "visible" : "hidden";
      node.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%)`;
      if (visible) occupied.push({ x, y, w, h });
    }
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
    canvas.removeEventListener("pointerleave", pointerLeave);
    canvas.removeEventListener("pointercancel", pointerCancel);
    canvas.removeEventListener("pointerup", pick);
    canvas.removeEventListener("webglcontextlost", lost);
    kit.dispose();
    releaseModels();
    sun.shadow.dispose();
    renderer.dispose();
    canvas.remove();
    labels.remove();
  }
  function lost(event: Event) {
    event.preventDefault();
    dispose();
    unavailable();
  }
  canvas.addEventListener("webglcontextlost", lost);
  return {
    update(residents, post, storeEpoch) {
      if (disposed) return;
      letters = post;
      if (epoch !== storeEpoch) {
        epoch = storeEpoch;
        let storage: Storage | undefined;
        try {
          storage = window.localStorage;
        } catch {
          /* Browsing may deny storage. */
        }
        allocate = createPlotAllocator(storeEpoch ?? "", storage);
        identities = undefined;
        walked.clear();
        selected = null;
      }
      const next = JSON.stringify(
        residents
          .map(({ id, name }) => [id, name])
          .sort((a, b) => a[0].localeCompare(b[0])),
      );
      if (next !== identities) {
        rebuild(residents);
        identities = next;
      }
    },
    select,
    overview: view.overview,
    zoom: view.zoom,
    rotate: view.rotate,
    dispose,
  };
}
