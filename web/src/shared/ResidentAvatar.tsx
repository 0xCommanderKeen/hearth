import { useEffect, useRef, useState } from "react";
import { visualIdentity } from "../features/hamlet/village/art.js";
import { requestPortrait } from "../features/hamlet/village/portraits";

/** Decorative beside a resident's visible name; status remains separate. */
export function ResidentAvatar({
  id,
  name,
  size = "small",
}: {
  id: string;
  name: string;
  size?: "small" | "large";
}) {
  const host = useRef<HTMLSpanElement>(null);
  const [portrait, setPortrait] = useState<{ id: string; url: string } | null>(
    null,
  );
  useEffect(() => {
    let cancel = () => {};
    let alive = true;
    const request = () => {
      cancel = requestPortrait(id, (url) => {
        if (alive) setPortrait(url ? { id, url } : null);
      });
    };
    // Off-screen roster entries do not allocate graphics work.
    const observer =
      typeof IntersectionObserver === "undefined"
        ? null
        : new IntersectionObserver(
            (entries) => {
              if (entries.some((e) => e.isIntersecting)) {
                observer?.disconnect();
                request();
              }
            },
            { rootMargin: "80px" },
          );
    if (observer && host.current) observer.observe(host.current);
    return () => {
      alive = false;
      cancel();
      observer?.disconnect();
    };
  }, [id]);
  const url = portrait?.id === id ? portrait.url : null;
  return (
    <span
      ref={host}
      className={`character-avatar character-avatar-${size}`}
      style={{ backgroundColor: visualIdentity(id).accent }}
      aria-hidden="true"
    >
      {url ? (
        <img src={url} alt="" onError={() => setPortrait(null)} />
      ) : (
        <span>{Array.from(name.trim())[0]?.toLocaleUpperCase() ?? "?"}</span>
      )}
    </span>
  );
}
