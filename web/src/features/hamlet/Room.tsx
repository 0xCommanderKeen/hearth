import { useEffect, useRef, useState } from "react";
import { placeByIdentity, workersAt } from "./village/places";
import { WORKROOM_PAGE_SIZE } from "./village/workroom";
import { visualIdentity } from "./village/art.js";
import type { Snapshot } from "../../shared/client";
import {
  createRoomScene,
  type RoomScene,
  type RoomTargets,
} from "./village/room";

export function Room({
  identity,
  snapshot,
  connected,
  active,
  lighter,
  onBack,
}: {
  identity: string;
  snapshot: Snapshot;
  connected: boolean;
  active: boolean;
  lighter: boolean;
  onBack(): void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const scene = useRef<RoomScene | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const townhall = identity === "#townhall";
  const place = placeByIdentity(identity);
  const shared = townhall || !!place;
  const [workerPage, setWorkerPage] = useState(0);
  const [selectedWorker, setSelectedWorker] = useState<string | null>(null);
  const occupants = workersAt(snapshot, identity);
  const pages = Math.max(1, Math.ceil(occupants.length / WORKROOM_PAGE_SIZE));
  const page = Math.min(workerPage, pages - 1);
  const shown = occupants.slice(
    page * WORKROOM_PAGE_SIZE,
    (page + 1) * WORKROOM_PAGE_SIZE,
  );
  const inspected = selectedWorker
    ? shown.find((w) => w.resident.id === selectedWorker)
    : shown[0];
  const resident = snapshot.residents.find(
    (r) => identity === `#residents/${encodeURIComponent(r.id)}`,
  );
  const archived = resident?.lifecycle?.state === "archived";
  const available = shared || (!!resident && !archived);
  const targets: RoomTargets = place
    ? {
        work: {
          label:
            place.key === "post"
              ? "Sorting tables · tasks & letters"
              : "Work desks · tasks & results",
          href: "#tasks",
        },
        shelf: {
          label:
            place.key === "research"
              ? "Reference shelves · inputs"
              : place.key === "post"
                ? "Post shelves · communications"
                : "Supply shelves · skills",
          href:
            place.key === "research"
              ? "#inputs"
              : place.key === "post"
                ? "#communications"
                : "#skills",
        },
        letters: {
          label:
            place.key === "post"
              ? "Noticeboard · inbox"
              : "Noticeboard · activity",
          href: place.key === "post" ? "#inbox" : "#activity",
        },
      }
    : townhall
      ? {
          work: {
            label: "Work table · household tasks & results",
            href: "#tasks",
          },
          shelf: {
            label: "Ledger shelf · household & allowances",
            href: "#townhall",
          },
          letters: { label: "Letter cabinet · inbox", href: "#inbox" },
        }
      : {
          work: {
            label: "Desk · recorded work",
            href: `${identity}?panel=work`,
          },
          shelf: {
            label: "Journal shelf · journal",
            href: `${identity}?panel=journal`,
          },
          letters: {
            label: "Letter cabinet · letters",
            href: `${identity}?panel=letters`,
          },
        };
  useEffect(() => {
    if (!host.current || !available) return;
    setUnavailable(false);
    try {
      scene.current = createRoomScene(
        host.current,
        townhall,
        targets,
        () => setUnavailable(true),
        resident?.id,
        shared
          ? {
              kind: place?.key ?? "townhall",
              onSelectResident: setSelectedWorker,
            }
          : undefined,
      );
    } catch {
      setUnavailable(true);
    }
    return () => {
      scene.current?.dispose();
      scene.current = null;
    };
    // Record updates never recreate furniture or its renderer.
  }, [identity, available]);
  useEffect(() => {
    scene.current?.active(active);
    if (active) heading.current?.focus({ preventScroll: true });
  }, [active, available]);
  useEffect(() => {
    scene.current?.occupancy?.(
      connected &&
        !!resident &&
        ["idle", "ready", "paused"].includes(resident.presence),
    );
  }, [connected, resident?.presence, identity, available]);
  useEffect(() => {
    if (shared)
      scene.current?.workers?.(
        shown.map(({ resident: r, run }) => ({
          id: r.id,
          name: r.name,
          action: run.action?.label ?? "Running work",
        })),
        connected,
      );
  }, [snapshot, connected, identity, available, page]);
  useEffect(() => {
    scene.current?.lighter(lighter);
  }, [lighter, identity, available]);
  return (
    <section className="hamlet-room" aria-label="Building interior">
      <nav className="room-breadcrumb" aria-label="Room breadcrumb">
        <button onClick={onBack}>Back to village</button>
        <span aria-hidden="true">/</span>
        <span>
          {townhall
            ? "Townhall"
            : (place?.name ??
              `${resident?.name ?? "Unavailable resident"}’s home`)}
        </span>
      </nav>
      <h2 ref={heading} tabIndex={-1}>
        {townhall
          ? "The household room"
          : (place?.name ?? `${resident?.name ?? "Resident"}’s home`)}
      </h2>
      {!available ? (
        <p className="notice">
          {archived
            ? "This resident has been archived. Their room is closed; recorded history and unresolved holds remain available."
            : "This resident is no longer available in the snapshot. No other resident has taken their place."}{" "}
          <a href={resident ? identity : "#residents-archived"}>
            Open resident history →
          </a>
        </p>
      ) : (
        <>
          <p className="room-caption">
            {townhall
              ? "A place for household work, allowances and correspondence."
              : place?.description ||
                resident?.purpose ||
                "A home for this resident’s recorded work."}{" "}
            {shared
              ? "Select a resident or their work spot to inspect the recorded work."
              : "Select furniture or use the record links below."}
          </p>
          <div className={shared ? "workroom-layout" : undefined}>
            <div className="room-canvas" ref={host} hidden={unavailable} />
            {shared && (
              <section
                className="workroom-roster"
                aria-label="Residents working here"
              >
                <h3>
                  {connected ? "Working here" : "Last known work"}{" "}
                  <span>· {occupants.length}</span>
                </h3>
                {occupants.length ? (
                  <>
                    <p className="workroom-caption">
                      {connected
                        ? "Select a resident for details."
                        : "Disconnected · figures are paused."}
                    </p>
                    <div className="workroom-people">
                      {shown.map(({ resident: r, run }) => (
                        <button
                          key={r.id}
                          aria-pressed={inspected?.resident.id === r.id}
                          onClick={() => setSelectedWorker(r.id)}
                        >
                          <span
                            className="resident-swatch"
                            style={{ background: visualIdentity(r.id).accent }}
                            aria-hidden="true"
                          />
                          {r.name}
                          <small>{run.action?.label ?? "Running work"}</small>
                        </button>
                      ))}
                    </div>
                    {pages > 1 && (
                      <nav
                        className="workroom-pages"
                        aria-label="Workstation pages"
                      >
                        <button
                          disabled={page === 0}
                          onClick={() => {
                            setWorkerPage(page - 1);
                            setSelectedWorker(null);
                          }}
                        >
                          Previous residents
                        </button>
                        <span>
                          {page * WORKROOM_PAGE_SIZE + 1}–
                          {Math.min(
                            (page + 1) * WORKROOM_PAGE_SIZE,
                            occupants.length,
                          )}{" "}
                          of {occupants.length}
                        </span>
                        <button
                          disabled={page === pages - 1}
                          onClick={() => {
                            setWorkerPage(page + 1);
                            setSelectedWorker(null);
                          }}
                        >
                          Next residents
                        </button>
                      </nav>
                    )}
                    {inspected ? (
                      <article
                        className="workroom-detail"
                        aria-label={`${inspected.resident.name}'s work`}
                        aria-live="polite"
                      >
                        <h4>{inspected.resident.name}</h4>
                        <p>
                          {inspected.task?.instruction ??
                            "Task instructions are outside the available snapshot."}
                          {inspected.task?.instruction_truncated && "…"}
                        </p>
                        <dl>
                          <dt>
                            {connected
                              ? "Last recorded action"
                              : "Last known action"}
                          </dt>
                          <dd>
                            {inspected.run.action?.label ?? "Running work"}
                          </dd>
                          {inspected.run.action && (
                            <>
                              <dt>Recorded at</dt>
                              <dd>
                                <time
                                  dateTime={new Date(
                                    inspected.run.action.at * 1000,
                                  ).toISOString()}
                                >
                                  {new Date(
                                    inspected.run.action.at * 1000,
                                  ).toLocaleString()}
                                </time>
                              </dd>
                            </>
                          )}
                          <dt>Run status</dt>
                          <dd>{inspected.run.status}</dd>
                        </dl>
                        <a
                          href={`#runs/${encodeURIComponent(inspected.run.id)}`}
                        >
                          Open run & task →
                        </a>
                        <a
                          href={`#residents/${encodeURIComponent(inspected.resident.id)}`}
                        >
                          Resident profile →
                        </a>
                      </article>
                    ) : (
                      <p>
                        The selected resident is no longer on this page. Select
                        another resident to inspect their work.
                      </p>
                    )}
                  </>
                ) : (
                  <p>
                    No running work is recorded here. Residents appear when
                    their recorded work belongs in this building.
                  </p>
                )}
              </section>
            )}
          </div>
          {unavailable && (
            <p className="notice">
              Room graphics are unavailable. All record links and Back to
              village remain available.
            </p>
          )}
          <nav className="room-records" aria-label="Room records">
            {Object.values(targets).map((target, i) => (
              <a href={target.href} key={target.href}>
                <span aria-hidden="true">0{i + 1}</span>
                {target.label}
                <b aria-hidden="true">→</b>
              </a>
            ))}
          </nav>
        </>
      )}
      <p className="room-facts">
        {!connected && "Disconnected · showing last known records. "}
        {townhall
          ? snapshot.household
            ? `${snapshot.household.active_runs} active runs · ${snapshot.household.resident_count} residents recorded.`
            : "Household totals unavailable."
          : shared
            ? `${occupants.length} resident(s) ${connected ? "working here" : "last recorded here"}. Working motions illustrate the recorded activity.`
            : `Recorded status: ${resident?.presence === "interrupted" ? "Outcome unknown" : (resident?.presence ?? "unavailable")}.`}{" "}
        Rooms and furniture are a visual representation of record access, not
        evidence of physical occupancy.
      </p>
      {townhall && (
        <p className="notice">
          {snapshot.household
            ? `$${(snapshot.household.unknown / 1e6).toFixed(2)} held for unknown usage.`
            : "Household accounting unavailable."}{" "}
          {snapshot.residents.reduce(
            (total, row) => total + (row.unresolved_runs ?? 0),
            0,
          )}{" "}
          unresolved run(s), including archived residents. Accounting holds
          remain until resolved in the records.
        </p>
      )}
      {snapshot.restore_hold && (
        <p className="notice">Restore hold · household mutations are held.</p>
      )}
      {!!resident?.unresolved_runs && (
        <p className="notice">
          {resident.unresolved_runs} unresolved run(s). Accounting holds remain.
        </p>
      )}
    </section>
  );
}
