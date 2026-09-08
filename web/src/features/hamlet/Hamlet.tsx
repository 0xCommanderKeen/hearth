import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { createArtKit } from "./village/art.js";
import type { LetterEvent, Snapshot } from "../../shared/client";

// How long one villager takes to walk from a door to a neighbour's, and how many walks
// the village shows at once. The rest wait their turn rather than being invented away.
const WALK_MS = 4200;
const WALKS_AT_ONCE = 3;

// Original Warren miniature models, shared here without its operational layer.
export function Hamlet({
  snapshot,
  connected,
}: {
  snapshot: Snapshot;
  connected: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [unavailable, setUnavailable] = useState(false);
  // The post as the server reported it, read by the animation loop rather than by the
  // scene's own effect: new letters must not rebuild the village.
  const post = useRef<LetterEvent[]>([]);
  post.current = snapshot.letters ?? [];
  // Every event is walked once. A snapshot repeats what it already reported, and the
  // scene is rebuilt whenever a resident arrives, so without this a single letter would
  // be walked again on every refresh — activity nobody performed.
  const walked = useRef<Set<string>>(new Set());
  const letters = snapshot.letters ?? [];
  // The operator stands at Townhall and has no resident row; everyone else is named.
  const name = (id: string | null) =>
    id === null
      ? "Townhall"
      : (snapshot.residents.find((r) => r.id === id)?.name ?? id);
  const villageResidents = snapshot.residents.filter(
    (r) => r.lifecycle?.state !== "archived",
  );
  const archivedUnresolved = snapshot.residents.filter(
    (r) => r.lifecycle?.state === "archived" && (r.unresolved_runs ?? 0) > 0,
  );
  const identities = villageResidents.map((r) => r.id).join("\0");
  useEffect(() => {
    if (!host.current) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch {
      setUnavailable(true);
      return;
    }
    setUnavailable(false);
    const element = host.current;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#cbd5c1");
    const rows = Math.max(1, Math.ceil((villageResidents.length + 1) / 4));
    const depth = Math.max(19, rows * 5 + 10);
    const centerZ = -(rows - 1) * 2.5;
    const distance = Math.max(19, depth * 1.25);
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, distance * 8);
    camera.position.set(distance * 0.74, distance * 0.8, centerZ + distance);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    element.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0, centerZ);
    controls.minDistance = 10;
    controls.maxDistance = distance * 3;
    controls.maxPolarAngle = Math.PI / 2.4;
    controls.enablePan = true;
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
    const groundGeometry = new THREE.BoxGeometry(26, 0.5, depth);
    const groundMaterial = new THREE.MeshStandardMaterial({
      color: "#98ad83",
      roughness: 1,
    });
    const ground = new THREE.Mesh(groundGeometry, groundMaterial);
    ground.position.set(0, -0.3, centerZ);
    ground.receiveShadow = true;
    scene.add(ground);
    const pathGeometry = new THREE.BoxGeometry(19, 0.04, 1.5);
    const pathMaterial = new THREE.MeshStandardMaterial({ color: "#dbc8a3" });
    const path = new THREE.Mesh(pathGeometry, pathMaterial);
    path.position.z = 1.8;
    path.receiveShadow = true;
    scene.add(path);
    const targets: THREE.Object3D[] = [];
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
      scene.add(object);
    }
    // Where a letter is handed over. The operator has no home in the village, so a
    // letter it wrote leaves from Townhall — the one door it actually stands at.
    const doors = new Map<string, THREE.Vector3>();
    building("townhall", "lodge", 7.5, -2, "#townhall");
    doors.set("operator", new THREE.Vector3(7.5, 0, -2 + 2.2));
    const square = kit.building({
      id: "square",
      kind: "square",
      width: 3,
      depth: 3,
    });
    square.position.set(0, 0, 4);
    scene.add(square);
    villageResidents.forEach((r, i) => {
      // The first row reserves its last plot for Townhall.
      const slot = i < 3 ? i : i + 1;
      const x = -7.5 + (slot % 4) * 5;
      const z = -2 - Math.floor(slot / 4) * 5;
      building(r.id, "home", x, z, `#residents/${encodeURIComponent(r.id)}`);
      const person = kit.agent({ id: r.id });
      person.position.set(x, 0, z + 2.2);
      person.userData.href = `#residents/${encodeURIComponent(r.id)}`;
      targets.push(person);
      scene.add(person);
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
      scene.add(tree);
    });
    const ray = new THREE.Raycaster();
    let down = { x: 0, y: 0 };
    const pointerDown = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY };
    };
    const pick = (e: PointerEvent) => {
      if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return;
      const rect = renderer.domElement.getBoundingClientRect();
      ray.setFromCamera(
        new THREE.Vector2(
          ((e.clientX - rect.left) / rect.width) * 2 - 1,
          (-(e.clientY - rect.top) / rect.height) * 2 + 1,
        ),
        camera,
      );
      let hit: THREE.Object3D | null =
        ray.intersectObjects(targets, true)[0]?.object ?? null;
      while (hit && !hit.userData.href) hit = hit.parent;
      if (hit) window.location.hash = hit.userData.href;
    };
    renderer.domElement.addEventListener("pointerdown", pointerDown);
    renderer.domElement.addEventListener("pointerup", pick);
    const resize = new ResizeObserver(() => {
      camera.aspect = element.clientWidth / element.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(element.clientWidth, element.clientHeight);
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
      for (let index = post.current.length - 1; index >= 0; index -= 1) {
        if (walks.length >= WALKS_AT_ONCE) return;
        const event = post.current[index];
        const key = `${event.kind}:${event.task_id}`;
        if (walked.current.has(key)) continue;
        const from = doors.get(event.from_resident_id ?? "operator");
        const to = doors.get(event.to_resident_id ?? "operator");
        walked.current.add(key);
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
      open(at);
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
    return () => {
      renderer.setAnimationLoop(null);
      walks.forEach((walk) => scene.remove(walk.person));
      resize.disconnect();
      controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", pointerDown);
      renderer.domElement.removeEventListener("pointerup", pick);
      kit.dispose();
      groundGeometry.dispose();
      groundMaterial.dispose();
      pathGeometry.dispose();
      pathMaterial.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [identities]);
  return (
    <section className="hamlet-scene" aria-label="Hamlet village">
      <div className="scene-toolbar">
        <span>HAMLET · 3D VILLAGE</span>
        <span>Drag to orbit · Scroll to zoom · Select a home</span>
      </div>
      <div
        ref={host}
        className="scene-canvas"
        role="img"
        aria-label={`${villageResidents.map((r) => `${r.name}'s home`).join(", ") || "Empty village"}. Select a resident using the links below.`}
      />
      {unavailable && (
        <p className="notice">
          3D is unavailable in this browser. Resident profiles remain available
          below.
        </p>
      )}
      {!!letters.length && (
        <ol className="scene-post" aria-label="Letters walked in the village">
          {letters.map((event) => (
            <li key={`${event.kind}:${event.task_id}`}>
              <strong>{name(event.from_resident_id)}</strong>
              <span aria-hidden="true">→</span>
              <strong>{name(event.to_resident_id)}</strong>
              <span>
                {event.kind === "letter_sent"
                  ? "carried a letter"
                  : "carried the answer"}{" "}
                · {event.title}
              </span>
            </li>
          ))}
        </ol>
      )}
      {archivedUnresolved.map((r) => (
        <p className="notice" key={r.id}>
          <a href={`#residents/${encodeURIComponent(r.id)}`}>{r.name}</a> is
          archived with {r.unresolved_runs} unresolved run(s). Accounting holds
          remain.
        </p>
      ))}
      <div className="scene-residents">
        <a href="#townhall">Townhall →</a>
        <a href="#residents-archived">Archived residents & history →</a>
        {villageResidents.map((r) => (
          <a key={r.id} href={`#residents/${encodeURIComponent(r.id)}`}>
            {r.name} · {connected ? r.presence : "disconnected"} →
          </a>
        ))}
      </div>
    </section>
  );
}
