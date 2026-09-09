import { useEffect, useRef, useState } from "react";
import type { Snapshot } from "../../shared/client";
import {
  createRoomScene,
  type RoomScene,
  type RoomTarget,
} from "./village/room";

export function Room({
  identity,
  snapshot,
  connected,
  active,
  onBack,
}: {
  identity: string;
  snapshot: Snapshot;
  connected: boolean;
  active: boolean;
  onBack(): void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const scene = useRef<RoomScene | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const townhall = identity === "#townhall";
  const resident = snapshot.residents.find(
    (r) => identity === `#residents/${encodeURIComponent(r.id)}`,
  );
  const archived = resident?.lifecycle?.state === "archived";
  const available = townhall || (!!resident && !archived);
  const targets: RoomTarget[] = townhall
    ? [
        { label: "Work table · household tasks & results", href: "#tasks" },
        { label: "Ledger shelf · household & allowances", href: "#townhall" },
        { label: "Letter cabinet · inbox", href: "#inbox" },
      ]
    : [
        { label: "Desk · recorded work", href: `${identity}?panel=work` },
        { label: "Journal shelf · journal", href: `${identity}?panel=journal` },
        {
          label: "Letter cabinet · letters",
          href: `${identity}?panel=letters`,
        },
      ];
  useEffect(() => {
    if (!host.current || !available) return;
    setUnavailable(false);
    try {
      scene.current = createRoomScene(host.current, townhall, targets, () =>
        setUnavailable(true),
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
  return (
    <section className="hamlet-room" aria-label="Building interior">
      <nav className="room-breadcrumb" aria-label="Room breadcrumb">
        <button onClick={onBack}>Back to village</button>
        <span aria-hidden="true">/</span>
        <span>
          {townhall
            ? "Townhall"
            : `${resident?.name ?? "Unavailable resident"}’s home`}
        </span>
      </nav>
      <h2 ref={heading} tabIndex={-1}>
        {townhall
          ? "The household room"
          : `${resident?.name ?? "Resident"}’s home`}
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
              : resident?.purpose ||
                "A home for this resident’s recorded work."}{" "}
            Select furniture or use the record links below.
          </p>
          <div className="room-canvas" ref={host} />
          {unavailable && (
            <p className="notice">
              Room graphics are unavailable. All record links and Back to
              village remain available.
            </p>
          )}
          <nav className="room-records" aria-label="Room records">
            {targets.map((target, i) => (
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
          : `Recorded status: ${resident?.presence === "interrupted" ? "Outcome unknown" : (resident?.presence ?? "unavailable")}.`}{" "}
        Rooms and furniture are a visual representation of record access, not
        evidence of physical occupancy.
      </p>
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
