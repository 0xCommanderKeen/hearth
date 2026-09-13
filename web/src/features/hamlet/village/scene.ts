import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { createArtKit } from "./art.js";
import { createCameraController, ZOOM_LIMITS } from "./camera";
import {
  createPlotAllocator,
  streetNetwork,
  routePosition,
  type Point,
  type Plot,
} from "./layout";
import { selectionGesture } from "./gesture";
import { createActivity, letterKey, residentStatus } from "./activity";
import {
  PLACES,
  residentLocation,
  createResidentJourneys,
  type Visit,
} from "./places";
import { streamBaseline } from "../../../shared/client";
import type { Snapshot, Resident } from "../../../shared/client";

const WALK_MS = 4200;
const WALKS_AT_ONCE = 3;
export type VillageScene = {
  update(snapshot: Snapshot, connected: boolean, visible: boolean): void;
  select(id: string | null): void;
  preview?(id: string | null): void;
  active(visible: boolean): void;
  lighter(enabled: boolean): void;
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
  const activity = createActivity();
  const residentJourneys = createResidentJourneys();
  let pendingVisits: Visit[] = [];
  let network = streetNetwork([]);
  let visible = true;
  let dirty = true;
  const invalidate = () => {
    dirty = true;
  };
  controls.addEventListener("change", invalidate);
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
    invalidate();
    outlines.forEach(({ id, mesh }) => {
      mesh.visible = id === selected || id === hovered;
    });
    names.forEach(({ id, node }) => {
      node.classList.toggle("selected", id === selected);
      node.classList.toggle("hovered", id === hovered);
      node.setAttribute("aria-pressed", String(id === selected));
    });
  }
  let restoreCamera: (() => void) | null = null;
  function select(id: string | null) {
    if (id && id !== selected) {
      restoreCamera ??= view.capture();
      const anchor = names.find((name) => name.id === id)?.anchor;
      if (anchor) view.focus(anchor.clone().setY(0));
    } else if (!id) {
      restoreCamera?.();
      restoreCamera = null;
    }
    selected = id;
    highlight();
  }
  function choose(id: string) {
    select(id);
    onSelect(id);
  }

  let releaseModels = () => {};
  function rebuild(residents: Resident[]) {
    clearWalks();
    scene.remove(village);
    releaseModels();
    village = new THREE.Group();
    village.name = "village";
    const plots = allocate([
      ...residents.map((r) => r.id),
      ...PLACES.map((p) => p.id),
    ]);
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
    network = streetNetwork(plots);
    network.streets.forEach(({ x, z, width, depth }) =>
      street(x, z, width, depth),
    );
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
      node.className =
        kind === "home" ? "scene-label" : "scene-label scene-label-civic";
      node.textContent = name;
      node.title = name;
      node.dataset.identity = href;
      node.setAttribute("aria-label", `Select ${name}`);
      node.onclick = () => choose(href);
      node.onfocus = () => {
        hovered = href;
        highlight();
      };
      node.onblur = () => {
        hovered = null;
        highlight();
      };
      node.onpointerenter = () => {
        hovered = href;
        highlight();
      };
      node.onpointerleave = () => {
        hovered = null;
        highlight();
      };
      labels.appendChild(node);
      names.push({
        id: href,
        node,
        anchor: new THREE.Vector3(
          x,
          new THREE.Box3().setFromObject(object).max.y + 0.65,
          z,
        ),
      });
      targets.push(object);
      village.add(object);
    }
    // Where a letter is handed over. The operator has no home in the village, so a
    // letter it wrote leaves from Townhall — the one door it actually stands at.
    building("townhall", "lodge", 0, -6, "#townhall", "Townhall");
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
      const place = PLACES.find((p) => p.id === id);
      if (place) {
        building(id, place.kind, x, z, place.identity, place.name);
        return;
      }
      const r = byId.get(id)!;
      building(
        id,
        "home",
        x,
        z,
        `#residents/${encodeURIComponent(id)}`,
        r.name,
      );
      // Residents are indoors except during a bounded, observed journey.
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
    // Bounded planting around the square and Townhall, away from street centres.
    for (const [x, z] of [
      [-1.6, -1.5],
      [1.6, -1.5],
      [-1.6, 1.4],
      [1.6, 1.4],
      [-1.25, -4.05],
      [1.25, -4.05],
    ]) {
      const garden = kit.garden();
      garden.position.set(x, 0, z);
      village.add(garden);
    }
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
    if (!element.clientWidth || !element.clientHeight) return;
    const width = Math.max(1, element.clientWidth);
    const height = Math.max(1, element.clientHeight);
    view.resize(width / height);
    renderer.setSize(width, height);
    invalidate();
    // Sizing clears the backing buffer after RAF; repaint before the next paint.
    draw();
  });
  resize.observe(element);
  const walks: {
    person: THREE.Group;
    path: Point[];
    started: number;
    visit?: Visit;
  }[] = [];
  function clearWalks() {
    if (walks.length) invalidate();
    walks.forEach((walk) => scene.remove(walk.person));
    walks.length = 0;
    activity.clear();
    pendingVisits = [];
  }
  const animate = () => {
    const at = performance.now();
    if (document.hidden || !visible) return;
    if (motion.matches) clearWalks();
    else
      while (walks.length < WALKS_AT_ONCE) {
        const visit = pendingVisits.shift();
        if (visit) {
          if (!residentJourneys.current(visit)) continue;
          const path = network.route(visit.from, visit.to);
          if (!path) continue;
          const person = kit.agent({ id: visit.residentId });
          person.userData.visit = visit;
          person.position.set(path[0].x, 0, path[0].z);
          scene.add(person);
          walks.push({ person, path, started: at, visit });
          continue;
        }
        const event = activity.take();
        if (!event) break;
        // A postal courier represents delivery, not a second copy of the sender.
        const first = network.route(event.from_resident_id, "@post");
        const last = network.route("@post", event.to_resident_id);
        if (!first || !last) continue;
        const path = [...first, ...last.slice(1)];
        const person = kit.agent({ id: "postal-courier", letter: true });
        person.userData.letter = letterKey(event);
        person.position.set(path[0].x, 0, path[0].z);
        scene.add(person);
        walks.push({ person, path, started: at });
      }
    if (walks.length) invalidate();
    for (let index = walks.length - 1; index >= 0; index--) {
      const walk = walks[index];
      const travelled = (at - walk.started) / WALK_MS;
      if (travelled >= 1) {
        scene.remove(walk.person);
        walks.splice(index, 1);
        continue;
      }
      const p = routePosition(walk.path, travelled),
        ahead = routePosition(walk.path, Math.min(1, travelled + 0.001));
      walk.person.position.set(p.x, 0, p.z);
      walk.person.lookAt(ahead.x, 0, ahead.z);
      const stride = Math.sin((at - walk.started) / 110) * 0.48;
      walk.person.userData.legs.forEach((leg: THREE.Group, i: number) => {
        leg.rotation.x = i ? -stride : stride;
      });
      walk.person.userData.arms.forEach((arm: THREE.Group, i: number) => {
        arm.rotation.x = i ? stride : -stride;
      });
      walk.person.position.y = Math.abs(stride) * 0.055;
    }
    controls.update();
    draw();
  };
  function draw() {
    if (disposed || !visible || document.hidden || !dirty) return;
    dirty = false;
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
  }
  function visibility() {
    if (document.hidden || !visible) clearWalks();
    invalidate();
    if (!disposed)
      renderer.setAnimationLoop(visible && !document.hidden ? animate : null);
  }
  document.addEventListener("visibilitychange", visibility);
  renderer.setAnimationLoop(document.hidden ? null : animate);

  let disposed = false;
  function dispose() {
    if (disposed) return;
    disposed = true;
    renderer.setAnimationLoop(null);
    resize.disconnect();
    document.removeEventListener("visibilitychange", visibility);
    controls.removeEventListener("change", invalidate);
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
    if (!renderer.getContext().isContextLost()) renderer.forceContextLoss();
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
    update(snapshot, connected, isVisible) {
      if (disposed) return;
      const residents = snapshot.residents.filter(
        (r) => r.lifecycle?.state !== "archived",
      );
      const storeEpoch = snapshot.epoch;
      visible = isVisible;
      invalidate();
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
        select(null);
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
      const statuses = new Map(
        residents.map((r) => [
          `#residents/${encodeURIComponent(r.id)}`,
          { name: r.name, ...residentStatus(r, connected) },
        ]),
      );
      names.forEach(({ id, node }) => {
        const status = statuses.get(id);
        if (!status) {
          const place = PLACES.find((p) => p.identity === id);
          const destination = id === "#townhall" ? null : place?.id;
          const count = residents.filter((r) => {
            const location = residentLocation(r, snapshot);
            return location.run && location.destination === destination;
          }).length;
          const name = place?.name ?? "Townhall";
          node.textContent = name + (count ? ` · ${count}` : "");
          node.title = `${name} · ${connected ? "" : "last known: "}${count} working`;
          node.setAttribute("aria-label", `Select ${name}`);
          return;
        }
        node.replaceChildren(document.createTextNode(status.name));
        const label = document.createElement("small");
        label.textContent = status.text;
        label.className = "scene-label-status";
        node.appendChild(label);
        node.dataset.status = status.tone;
        node.title = `${status.name} · ${status.text}`;
        node.setAttribute(
          "aria-label",
          `Select ${status.name} · ${status.text}`,
        );
      });
      const reset = activity.observe(
        snapshot,
        connected,
        visible && !document.hidden,
        motion.matches,
        streamBaseline(snapshot),
      );
      const moves = residentJourneys.observe(
        snapshot,
        !reset && connected && visible && !document.hidden && !motion.matches,
      );
      if (reset) {
        walks.forEach((walk) => scene.remove(walk.person));
        walks.length = 0;
        pendingVisits = [];
      } else {
        for (let i = walks.length - 1; i >= 0; i--) {
          if (walks[i].visit && !residentJourneys.current(walks[i].visit!)) {
            scene.remove(walks[i].person);
            walks.splice(i, 1);
          }
        }
        pendingVisits = [
          ...pendingVisits.filter((v) => residentJourneys.current(v)),
          ...moves,
        ].slice(0, 12);
      }
    },
    select,
    preview(id) {
      hovered = id;
      highlight();
    },
    active(isVisible) {
      visible = isVisible;
      visibility();
    },
    lighter(enabled) {
      if (disposed) return;
      renderer.setPixelRatio(
        Math.min(window.devicePixelRatio, enabled ? 1 : 2),
      );
      renderer.shadowMap.enabled = !enabled;
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          const materials = Array.isArray(object.material)
            ? object.material
            : [object.material];
          materials.forEach((material) => {
            material.needsUpdate = true;
          });
        }
      });
      invalidate();
    },
    overview: () => {
      view.overview();
      invalidate();
    },
    zoom: (factor) => {
      view.zoom(factor);
      invalidate();
    },
    rotate: (direction) => {
      view.rotate(direction);
      invalidate();
    },
    dispose,
  };
}
