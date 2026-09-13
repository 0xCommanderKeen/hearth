import * as THREE from "three";
import { createArtKit, visualIdentity } from "./art.js";
import { selectionGesture } from "./gesture";
import type { RoomScene, RoomTargets } from "./room";

export const WORKROOM_PAGE_SIZE = 6;
export type WorkroomWorker = { id: string; name: string; action: string };
export type WorkroomOptions = {
  kind: "workshop" | "research" | "post" | "townhall";
  onSelectResident(id: string): void;
};

/** A shared cutaway has six work spots. Further residents remain available by page. */
export function createWorkroomScene(
  element: HTMLElement,
  targets: RoomTargets,
  unavailable: () => void,
  options: WorkroomOptions,
): RoomScene {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const canvas = renderer.domElement;
  canvas.setAttribute("aria-hidden", "true");
  element.appendChild(canvas);
  const labels = document.createElement("div");
  labels.className = "room-people-labels";
  element.appendChild(labels);
  const scene = new THREE.Scene();
  scene.name = `workroom:${options.kind}`;
  scene.background = new THREE.Color("#cbd5c1");
  scene.add(new THREE.HemisphereLight("#fff5df", "#748267", 2.8));
  const sun = new THREE.DirectionalLight("#fff0cd", 3);
  sun.position.set(2, 9, 6);
  scene.add(sun);
  const camera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 100);
  camera.position.set(11, 15, 18);
  camera.lookAt(0, 0.8, 0);
  camera.updateMatrixWorld();
  const kit = createArtKit();
  const geometry = new THREE.BoxGeometry(1, 1, 1);
  const materials = new Map<string, THREE.MeshStandardMaterial>();
  const picks: THREE.Object3D[] = [];
  const accent = {
    workshop: "#477a77",
    research: "#5d748d",
    post: "#a35f49",
    townhall: "#887148",
  }[options.kind];
  const wood = "#695449",
    paper = "#f8ebcd",
    brass = "#d4ad66";
  function box(
    root: THREE.Object3D,
    color: string,
    position: number[],
    scale: number[],
  ) {
    if (!materials.has(color))
      materials.set(
        color,
        new THREE.MeshStandardMaterial({ color, roughness: 0.9 }),
      );
    const mesh = new THREE.Mesh(geometry, materials.get(color));
    mesh.position.set(position[0], position[1], position[2]);
    mesh.scale.set(scale[0], scale[1], scale[2]);
    root.add(mesh);
    return mesh;
  }
  const shell = new THREE.Group();
  scene.add(shell);
  box(shell, "#a17b59", [0, -0.16, 0], [12.3, 0.4, 13]);
  for (let i = 0; i < 16; i++)
    box(
      shell,
      i % 2 ? "#d0ad7c" : "#c5a174",
      [-5.65 + i * 0.75, 0.07, 0],
      [0.72, 0.06, 12.6],
    );
  box(shell, "#eee1c4", [0, 1.65, -6.3], [12.3, 3.3, 0.2]);
  box(shell, "#eee1c4", [-6, 1.65, 0], [0.2, 3.3, 12.8]);
  box(shell, wood, [0, 3.3, -6.3], [12.4, 0.15, 0.3]);
  box(shell, wood, [-6, 3.3, 0], [0.3, 0.15, 12.9]);
  box(shell, accent, [-5.88, 2, -2], [0.1, 1.7, 2.5]);
  box(shell, "#ffd788", [-5.8, 2, -2], [0.06, 1.4, 2.2]);
  for (const z of [-2.7, -2, -1.3])
    box(shell, paper, [-5.74, 2, z], [0.06, 1.45, 0.06]);
  // Building-specific supplies make the function readable before anyone arrives.
  const supplies = new THREE.Group();
  supplies.userData.href = targets.shelf.href;
  supplies.userData.label = targets.shelf.label;
  picks.push(supplies);
  scene.add(supplies);
  box(supplies, wood, [1.8, 1.3, -5.8], [5.2, 2.6, 0.7]);
  for (let row = 0; row < 3; row++) {
    box(supplies, paper, [1.8, 0.35 + row * 0.8, -5.35], [5, 0.08, 0.65]);
    for (let i = 0; i < 8; i++) {
      const color =
        options.kind === "post" ? paper : [accent, brass, "#be6549"][i % 3];
      box(
        supplies,
        color,
        [-0.4 + i * 0.6, 0.65 + row * 0.8, -5.55],
        options.kind === "workshop" ? [0.4, 0.35, 0.4] : [0.25, 0.55, 0.42],
      );
    }
  }
  const board = new THREE.Group();
  board.userData.href = targets.letters.href;
  board.userData.label = targets.letters.label;
  picks.push(board);
  scene.add(board);
  box(board, accent, [-3.6, 2, -6.1], [2.6, 1.7, 0.12]);
  for (const x of [-4.4, -3.6, -2.8])
    box(board, paper, [x, 2, -6], [0.5, 1.1, 0.04]);
  const spots = Array.from({ length: WORKROOM_PAGE_SIZE }, (_, i) => {
    const x = i % 2 ? 2.7 : -2.5,
      z = -3.1 + Math.floor(i / 2) * 3.3;
    const group = new THREE.Group();
    group.position.set(x, 0, z);
    group.userData.href = targets.work.href;
    group.userData.label = targets.work.label;
    picks.push(group);
    scene.add(group);
    box(group, wood, [0, 1.05, 0], [2.65, 0.2, 1.05]);
    for (const dx of [-1, 1])
      for (const dz of [-0.35, 0.35])
        box(group, wood, [dx, 0.55, dz], [0.12, 1, 0.12]);
    if (options.kind === "research") {
      const left = box(group, paper, [-0.32, 1.2, 0], [0.62, 0.06, 0.65]);
      left.rotation.z = 0.15;
      const right = box(group, paper, [0.32, 1.2, 0], [0.62, 0.06, 0.65]);
      right.rotation.z = -0.15;
      box(group, accent, [0.9, 1.32, -0.2], [0.25, 0.32, 0.25]);
    } else if (options.kind === "post") {
      for (let j = 0; j < 3; j++) {
        box(
          group,
          paper,
          [-0.6 + j * 0.55, 1.2 + j * 0.025, 0],
          [0.43, 0.06, 0.32],
        );
        box(
          group,
          accent,
          [-0.5 + j * 0.55, 1.24 + j * 0.025, 0.06],
          [0.09, 0.015, 0.07],
        );
      }
    } else if (options.kind === "workshop") {
      box(group, accent, [0, 1.3, 0], [0.65, 0.35, 0.5]);
      box(group, brass, [0.7, 1.2, 0], [0.5, 0.07, 0.12]);
      box(group, wood, [0.85, 1.2, 0.12], [0.08, 0.07, 0.4]);
    } else {
      box(group, paper, [0, 1.2, 0], [1.2, 0.06, 0.65]);
      for (let j = 0; j < 3; j++)
        box(group, accent, [0, 1.24, -0.2 + j * 0.18], [0.8, 0.015, 0.035]);
    }
    return { group, x, z };
  });
  const workers = new Map<
    string,
    {
      person: THREE.Group;
      label: HTMLButtonElement;
      slot: number;
      worker: WorkroomWorker;
    }
  >();
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let disposed = false,
    visible = true,
    connected = true,
    lastFrame = 0;
  const projected = new THREE.Box3();
  for (const x of [-6.3, 6.3])
    for (const y of [-0.4, 3.5])
      for (const z of [-6.6, 6.6])
        projected.expandByPoint(
          new THREE.Vector3(x, y, z).applyMatrix4(camera.matrixWorldInverse),
        );
  const extent = projected.getSize(new THREE.Vector3());
  function draw() {
    if (
      disposed ||
      !visible ||
      document.hidden ||
      !element.clientWidth ||
      !element.clientHeight
    )
      return;
    const width = element.clientWidth,
      height = element.clientHeight,
      aspect = width / height;
    const half = Math.max(extent.y, extent.x / aspect) * 0.56;
    Object.assign(camera, {
      left: -half * aspect,
      right: half * aspect,
      top: half,
      bottom: -half,
    });
    camera.updateProjectionMatrix();
    if (
      canvas.clientWidth !== width ||
      canvas.clientHeight !== height ||
      canvas.width !== Math.floor(width * renderer.getPixelRatio()) ||
      canvas.height !== Math.floor(height * renderer.getPixelRatio())
    )
      renderer.setSize(width, height);
    renderer.render(scene, camera);
    for (const { label, slot } of workers.values()) {
      const spot = spots[slot];
      const p = new THREE.Vector3(spot.x, 2.25, spot.z + 0.9).project(camera);
      label.style.transform = `translate(${((p.x + 1) * width) / 2}px,${((1 - p.y) * height) / 2}px) translate(-50%,-100%)`;
    }
  }
  function pose(at: number, moving: boolean) {
    for (const { person, slot } of workers.values()) {
      const sway = moving ? Math.sin(at / 450 + slot * 1.8) * 0.16 : 0;
      const arms = person.userData.arms as THREE.Group[];
      arms[0].rotation.x = -0.9 + sway;
      arms[1].rotation.x =
        options.kind === "research" ? -0.9 - sway * 0.3 : -1.1 - sway;
    }
  }
  function animate(at: number) {
    if (
      disposed ||
      !visible ||
      !connected ||
      document.hidden ||
      motion.matches ||
      at - lastFrame < 33
    )
      return;
    lastFrame = at;
    pose(at, true);
    draw();
  }
  function playback() {
    if (disposed) return;
    const moving =
      visible &&
      !document.hidden &&
      connected &&
      !motion.matches &&
      workers.size > 0;
    renderer.setAnimationLoop(moving ? animate : null);
    if (!moving) pose(0, false);
    draw();
  }
  const resize = new ResizeObserver(draw);
  resize.observe(element);
  document.addEventListener("visibilitychange", playback);
  motion.addEventListener("change", playback);
  const gesture = selectionGesture(),
    ray = new THREE.Raycaster();
  function hit(event: PointerEvent) {
    const rect = canvas.getBoundingClientRect();
    ray.setFromCamera(
      new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        (-(event.clientY - rect.top) / rect.height) * 2 + 1,
      ),
      camera,
    );
    let object: THREE.Object3D | null =
      ray.intersectObjects(picks, true)[0]?.object ?? null;
    while (object && !object.userData.workerId && !object.userData.href)
      object = object.parent;
    return object;
  }
  const down = (event: PointerEvent) => gesture.down(event);
  const move = (event: PointerEvent) => {
    gesture.move(event);
    const object = hit(event);
    canvas.style.cursor = object ? "pointer" : "default";
    canvas.title = object?.userData.label ?? "Select a resident or work spot";
  };
  const up = (event: PointerEvent) => {
    if (!gesture.up(event)) return;
    const object = hit(event);
    if (object?.userData.workerId)
      options.onSelectResident(object.userData.workerId);
    else if (object?.userData.href) window.location.hash = object.userData.href;
  };
  const cancel = () => gesture.cancel();
  function remove(id: string) {
    const worker = workers.get(id);
    if (!worker) return;
    scene.remove(worker.person);
    picks.splice(picks.indexOf(worker.person), 1);
    worker.label.remove();
    delete spots[worker.slot].group.userData.workerId;
    spots[worker.slot].group.userData.label = targets.work.label;
    workers.delete(id);
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    renderer.setAnimationLoop(null);
    resize.disconnect();
    document.removeEventListener("visibilitychange", playback);
    motion.removeEventListener("change", playback);
    canvas.removeEventListener("pointerdown", down);
    canvas.removeEventListener("pointermove", move);
    canvas.removeEventListener("pointerup", up);
    canvas.removeEventListener("pointercancel", cancel);
    canvas.removeEventListener("webglcontextlost", lost);
    kit.dispose();
    geometry.dispose();
    materials.forEach((m) => m.dispose());
    renderer.dispose();
    if (!renderer.getContext().isContextLost()) renderer.forceContextLoss();
    canvas.remove();
    labels.remove();
    workers.clear();
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
  draw();
  return {
    workers(next, online) {
      if (disposed) return;
      connected = online;
      const page = next.slice(0, WORKROOM_PAGE_SIZE);
      for (const id of workers.keys())
        if (!page.some((w) => w.id === id)) remove(id);
      for (const worker of page) {
        if (!workers.has(worker.id)) {
          const occupied = new Set([...workers.values()].map((w) => w.slot));
          const slot = spots.findIndex((_, i) => !occupied.has(i));
          const spot = spots[slot];
          const person = kit.agent({
            id: worker.id,
            letter: options.kind === "post",
          });
          person.scale.multiplyScalar(1.8);
          person.position.set(spot.x, 0.13, spot.z + 1);
          person.rotation.y = Math.PI;
          person.userData.workerId = worker.id;
          scene.add(person);
          picks.push(person);
          const label = document.createElement("button");
          label.className = "room-person-label";
          label.onclick = () => options.onSelectResident(worker.id);
          label.style.borderColor = visualIdentity(worker.id).accent;
          labels.appendChild(label);
          workers.set(worker.id, { person, label, slot, worker });
        }
        const entry = workers.get(worker.id)!;
        entry.worker = worker;
        const title = `${worker.name} · ${online ? "" : "last known: "}${worker.action}`;
        entry.person.userData.label = title;
        entry.label.textContent = worker.name;
        entry.label.title = title;
        entry.label.setAttribute("aria-label", `Inspect ${title}`);
        spots[entry.slot].group.userData.workerId = worker.id;
        spots[entry.slot].group.userData.label = title;
      }
      pose(0, false);
      playback();
    },
    active(value) {
      visible = value;
      playback();
    },
    lighter(enabled) {
      if (disposed) return;
      renderer.setPixelRatio(
        Math.min(window.devicePixelRatio, enabled ? 1 : 2),
      );
      renderer.setSize(element.clientWidth, element.clientHeight);
      draw();
    },
    dispose,
  };
}
