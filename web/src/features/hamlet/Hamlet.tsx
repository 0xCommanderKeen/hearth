import { useEffect, useRef, useState } from "react";
import { createVillageScene, type VillageScene } from "./village/scene";
import { ContextPanel } from "./Panels";
import { PLACES, placeByIdentity, residentLocation } from "./village/places";
import { visualIdentity } from "./village/art.js";
import { residentStatus } from "./village/activity";
import { Room } from "./Room";
import type { Snapshot } from "../../shared/client";

// Original Warren miniature models, shared here without its operational layer.
export function Hamlet({
  snapshot,
  connected,
  active = true,
}: {
  snapshot: Snapshot;
  connected: boolean;
  active?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [inside, setInside] = useState(false);
  const [lighter, setLighter] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const origin = useRef<HTMLElement | null>(null);
  const selection = useRef(selected);
  selection.current = selected;
  const select = (identity: string) => {
    if (!selected) origin.current = document.activeElement as HTMLElement;
    setSelected(identity);
    scene.current?.select(identity);
  };
  const back = () => {
    setInside(false);
  };
  const close = () => {
    if (inside) {
      back();
      return;
    }
    setSelected(null);
    scene.current?.select(null);
    const target = origin.current?.isConnected ? origin.current : host.current;
    target?.focus({ preventScroll: true });
  };
  useEffect(() => {
    if (!active || !selected) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [active, selected, inside]);
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
      scene.current = createVillageScene(
        host.current,
        () => setUnavailable(true),
        (id) => {
          if (!selection.current) {
            const focused = document.activeElement;
            origin.current =
              focused instanceof HTMLButtonElement &&
              host.current?.contains(focused)
                ? focused
                : host.current;
          }
          setSelected(id);
        },
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
    scene.current?.update(snapshot, connected, active && !inside);
  }, [snapshot, connected, active, inside]);
  useEffect(() => {
    setSelected(null);
    setInside(false);
  }, [snapshot.epoch]);
  useEffect(() => {
    scene.current?.active(active && !inside);
  }, [active, inside]);
  useEffect(() => {
    scene.current?.lighter(lighter);
  }, [lighter]);
  return (
    <section
      hidden={!active}
      className="hamlet-scene"
      aria-label="Hamlet village"
    >
      <div hidden={inside}>
        <div className="scene-toolbar">
          <span>
            HAMLET <small> · {villageResidents.length} residents</small>
          </span>
          <details className="scene-help">
            <summary>View & controls</summary>
            <p>
              Drag to orbit · Shift-drag or one finger to pan · Scroll or pinch
              to zoom.
            </p>
            <p>
              Select a home or search below. Idle and ready residents are at
              home. Buildings represent recorded work; postal couriers
              illustrate new letters.
            </p>
            <label className="scene-rendering">
              <input
                type="checkbox"
                checked={lighter}
                onChange={(event) => setLighter(event.target.checked)}
              />
              Lighter graphics{" "}
              <small>Lower resolution, no village shadows</small>
            </label>
          </details>
        </div>
        <div
          className="scene-controls"
          role="group"
          aria-label="Village camera"
        >
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
        </div>
        <div
          tabIndex={-1}
          ref={host}
          className="scene-canvas"
          role="group"
          aria-label={`${villageResidents.map((r) => `${r.name}'s home`).join(", ") || "Empty village"}. Select a building here or in the directory below.`}
        />
        {unavailable && (
          <p className="notice">
            3D is unavailable in this browser. Resident profiles remain
            available below.
          </p>
        )}
        {/* The same events the walk is drawn from, in words. A letter whose two ends are
          not both homes in this village is listed here and not drawn, because there is
          no door to walk to; it is never dropped from the record. */}
        {!!letters.length && (
          <div className="scene-post-history">
            <p>
              {connected
                ? "Recent post · recorded history"
                : "Recent post · disconnected, last known history"}
              . Showing {letters.length} retained events
              {snapshot.limits?.letters
                ? ` (up to ${snapshot.limits.letters})`
                : ""}
              . Postal couriers illustrate newly observed letters only.
            </p>
            <ol className="scene-post" aria-label="Recent post">
              {letters.map((event) => (
                <li key={`${event.kind}:${event.task_id}`}>
                  <strong>{name(event.from_resident_id)}</strong>
                  <span aria-hidden="true">→</span>
                  <strong>{name(event.to_resident_id)}</strong>
                  <span>
                    {event.kind === "letter_sent"
                      ? "sent a letter"
                      : "answered a letter"}{" "}
                    · {event.title}
                  </span>
                  <time dateTime={new Date(event.at * 1000).toISOString()}>
                    {new Date(event.at * 1000).toLocaleString()}
                  </time>
                </li>
              ))}
            </ol>
          </div>
        )}
        {archivedUnresolved.map((r) => (
          <p className="notice" key={r.id}>
            <a href={`#residents/${encodeURIComponent(r.id)}`}>{r.name}</a> is
            archived with {r.unresolved_runs} unresolved run(s). Accounting
            holds remain.
          </p>
        ))}
        <div className="scene-find">
          <label htmlFor="hamlet-search">Find a resident</label>
          <input
            id="hamlet-search"
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search names or purpose…"
          />
          <small role="status">
            {
              snapshot.residents.filter((r) =>
                `${r.name} ${r.purpose ?? ""}`
                  .toLocaleLowerCase()
                  .includes(search.toLocaleLowerCase().trim()),
              ).length
            }{" "}
            found
          </small>
        </div>
        <div
          className="scene-directory"
          role="group"
          aria-label="Building directory"
        >
          {[{ identity: "#townhall", name: "Townhall" }, ...PLACES].map(
            (place) => (
              <button
                key={place.identity}
                className="scene-civic"
                aria-pressed={selected === place.identity}
                onPointerEnter={() => scene.current?.preview?.(place.identity)}
                onPointerLeave={() => scene.current?.preview?.(null)}
                onFocus={() => scene.current?.preview?.(place.identity)}
                onBlur={() => scene.current?.preview?.(null)}
                onClick={() => select(place.identity)}
              >
                Select {place.name}
              </button>
            ),
          )}
          {snapshot.residents
            .filter((r) =>
              `${r.name} ${r.purpose ?? ""}`
                .toLocaleLowerCase()
                .includes(search.toLocaleLowerCase().trim()),
            )
            .map((r) => {
              const identity = `#residents/${encodeURIComponent(r.id)}`;
              const location = residentLocation(r, snapshot);
              return (
                <button
                  key={r.id}
                  aria-pressed={selected === identity}
                  onPointerEnter={() => scene.current?.preview?.(identity)}
                  onPointerLeave={() => scene.current?.preview?.(null)}
                  onFocus={() => scene.current?.preview?.(identity)}
                  onBlur={() => scene.current?.preview?.(null)}
                  onClick={() => select(identity)}
                >
                  <span
                    className="resident-swatch"
                    style={{ background: visualIdentity(r.id).accent }}
                    aria-hidden="true"
                  />
                  Select {r.name}
                  <small>
                    {residentStatus(r, connected).text}
                    {r.lifecycle?.state === "archived"
                      ? " · archived"
                      : ` · ${connected ? location.label : "last known: " + location.label}`}
                  </small>
                </button>
              );
            })}
        </div>
      </div>
      {inside && selected && (
        <Room
          key={`${snapshot.epoch}:${selected}`}
          identity={selected}
          snapshot={snapshot}
          connected={connected}
          active={active}
          lighter={lighter}
          onBack={back}
        />
      )}
      {!inside && selected && (
        <ContextPanel
          key={`${snapshot.epoch}:${selected}`}
          identity={selected}
          snapshot={snapshot}
          connected={connected}
          active={active}
          onClose={close}
          onEnter={
            placeByIdentity(selected) ? undefined : () => setInside(true)
          }
        />
      )}
      <div className="scene-residents">
        <a href="#residents-archived">Archived residents &amp; history →</a>
      </div>
    </section>
  );
}
