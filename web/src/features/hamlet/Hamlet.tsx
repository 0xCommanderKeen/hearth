import { useEffect, useRef, useState } from "react";
import { createVillageScene, type VillageScene } from "./village/scene";
import type { Snapshot } from "../../shared/client";

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
  const scene = useRef<VillageScene | null>(null);
  useEffect(() => {
    if (!host.current) return;
    try {
      scene.current = createVillageScene(host.current, () =>
        setUnavailable(true),
      );
      setUnavailable(false);
    } catch {
      setUnavailable(true);
    }
    return () => {
      scene.current?.dispose();
      scene.current = null;
    };
  }, []);
  useEffect(() => {
    scene.current?.update(villageResidents, letters);
  }, [snapshot]);
  return (
    <section className="hamlet-scene" aria-label="Hamlet village">
      <div className="scene-toolbar">
        <span>HAMLET · 3D VILLAGE</span>
        <span>
          Drag to orbit · Two fingers to zoom / rotate · Select a home
        </span>
      </div>
      <div className="scene-controls" role="group" aria-label="Village camera">
        <button
          disabled={unavailable}
          onClick={() => scene.current?.overview()}
        >
          Overview
        </button>
        <button
          disabled={unavailable}
          aria-label="Zoom in"
          onClick={() => scene.current?.zoom(1.25)}
        >
          ＋
        </button>
        <button
          disabled={unavailable}
          aria-label="Zoom out"
          onClick={() => scene.current?.zoom(0.8)}
        >
          −
        </button>
        <button
          disabled={unavailable}
          aria-label="Rotate left"
          onClick={() => scene.current?.rotate(-1)}
        >
          ↶
        </button>
        <button
          disabled={unavailable}
          aria-label="Rotate right"
          onClick={() => scene.current?.rotate(1)}
        >
          ↷
        </button>
        <span>Shift-drag or one finger to pan · Scroll to zoom</span>
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
      {/* The same events the walk is drawn from, in words. A letter whose two ends are
          not both homes in this village is listed here and not drawn, because there is
          no door to walk to; it is never dropped from the record. */}
      {!!letters.length && (
        <ol className="scene-post" aria-label="Recent post">
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
