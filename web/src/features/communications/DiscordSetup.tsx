import { useEffect, useState } from "react";
import type { Resident, Runtimes } from "../../shared/client";
import { runtimeLabel } from "../../shared/runtimes";
import { time, words, type Config, type Scope, type Status } from "./api";
const powers = [
  {
    key: "read",
    label: "Read channel history",
    note: "Read bounded channel text; reading never starts work. General history needs Message Content access.",
  },
  {
    key: "listen",
    label: "Receive mentioned requests",
    note: "Allow a human guild member's verified mention to request ordinary work in this channel.",
  },
  {
    key: "reply",
    label: "Reply to mentioned requests",
    note: "Allow a reply to the source channel. Mention work requires both receive and reply grants.",
  },
  {
    key: "post",
    label: "Publish announcements",
    note: "Allow operator assignments and routines to publish here. External requests never gain this permission.",
  },
] as const;
const same = (left: Scope, right: Scope) =>
  left.connection_id === right.connection_id &&
  left.guild_id === right.guild_id &&
  left.channel_id === right.channel_id;
export function DiscordSetup({
  configs,
  status,
  residents,
  runtimes,
  residentId,
  disabled,
  onEdit,
  onSave,
}: {
  configs: Config[];
  status: Status;
  residents: Resident[];
  runtimes?: Runtimes;
  residentId?: string;
  disabled: boolean;
  onEdit: (value: Config) => void;
  onSave: (
    kind: string,
    id: string,
    revision: number,
    value: Record<string, unknown>,
  ) => Promise<boolean>;
}) {
  const connections = configs.filter(
    (item) => item.kind === "connection" && item.value.transport === "discord",
  );
  const routes = configs.filter(
    (item) =>
      item.kind === "route" &&
      connections.some(
        (connection) => connection.id === item.value.connection_id,
      ),
  );
  const [routeKey, setRouteKey] = useState("");
  const selected = routes.find((item) => item.id === routeKey);
  const [id, setId] = useState("");
  const [connection, setConnection] = useState("");
  const [resident, setResident] = useState(residentId ?? "");
  const [guild, setGuild] = useState("");
  const [channel, setChannel] = useState("");
  const [label, setLabel] = useState("");
  const [state, setState] = useState("pending");
  const [permission, setPermission] = useState<Record<string, boolean>>({});
  const current = residents.find((item) => item.id === resident);
  const grant = configs.find(
    (item) => item.kind === "grant" && item.id === resident,
  );
  const destination: Scope = {
    connection_id: connection,
    guild_id: guild,
    channel_id: channel,
  };
  const omittedGrant = status.configuration.some(
    (item) =>
      item.kind === "grant" && item.id === resident && item.value === null,
  );
  const incomplete = status.next_after !== null;
  useEffect(() => {
    if (selected) {
      const address = selected.value.address as Scope;
      setId(selected.id);
      setConnection(String(selected.value.connection_id));
      setResident(String(selected.value.resident_id));
      setGuild(address.guild_id ?? "");
      setChannel(address.channel_id ?? "");
      setLabel(String(selected.value.label ?? ""));
      setState(String(selected.value.state));
    }
  }, [selected?.id, selected?.value.revision]);
  useEffect(() => {
    setPermission(
      Object.fromEntries(
        powers.map(({ key }) => [
          key,
          Array.isArray(grant?.value[key]) &&
            (grant.value[key] as Scope[]).some((scope) =>
              same(scope, {
                connection_id: connection,
                guild_id: guild,
                channel_id: channel,
              }),
            ),
        ]),
      ),
    );
  }, [grant?.value.revision, resident, connection, guild, channel]);
  const routeValue = {
    connection_id: connection,
    resident_id: resident,
    address: { guild_id: guild, channel_id: channel },
    label,
    state,
    mode: selected?.value.mode ?? "dedicated",
    sender_policy: selected?.value.sender_policy ?? "guild_channel_humans",
    operator_ids: selected?.value.operator_ids ?? [],
  };
  async function saveRoute(e: React.FormEvent) {
    e.preventDefault();
    if (await onSave("route", id, selected?.value.revision ?? 0, routeValue))
      setRouteKey(id);
  }
  async function savePermissions(e: React.FormEvent) {
    e.preventDefault();
    const value = Object.fromEntries(
      powers.map(({ key }) => {
        const existing = (grant?.value[key] as Scope[] | undefined) ?? [];
        return [
          key,
          [
            ...existing.filter((scope) => !same(scope, destination)),
            ...(permission[key] ? [destination] : []),
          ],
        ];
      }),
    );
    await onSave("grant", resident, grant?.value.revision ?? 0, value);
  }
  const validDestination =
    !!connection && !!current && /^\d+$/.test(guild) && /^\d+$/.test(channel);
  return (
    <details className="discord-setup">
      <summary>Discord setup</summary>
      <p>
        Select installation records from this Hearth. No resident, runtime, live
        server or channel has been chosen for you. Use a dedicated test channel
        and synthetic text for first verification.
      </p>
      <h4>1. Bot connection</h4>
      <p>
        Create and install a dedicated Discord bot with only View Channel, Read
        Message History and Send Messages as needed. Check channel overrides.
        General history also needs Message Content access. Administrator is not
        required.
      </p>
      <p>
        The protected credential reference names a server-managed token slot
        outside resident instructions, source code and run mounts. Enter the bot
        user ID as its external identity. Discord egress belongs to the backend
        communications worker.
      </p>
      <button
        disabled={disabled}
        onClick={() =>
          onEdit({
            kind: "connection",
            id: "",
            value: {
              revision: 0,
              transport: "discord",
              bot_id: "",
              secret_ref: "",
              state: "pending",
              label: "",
            },
          })
        }
      >
        Create Discord connection
      </button>
      <p>
        Connection saves do not activate a consumer. Use the explicit activation
        and probe controls below after configuring the route and permissions.
      </p>
      <h4>2. Select the resident and channel</h4>
      <label>
        Discord route
        <select
          aria-label="Discord route"
          value={routeKey}
          disabled={disabled}
          onChange={(e) => {
            setRouteKey(e.target.value);
            if (!e.target.value) {
              setId("");
              setConnection("");
              setResident(residentId ?? "");
              setGuild("");
              setChannel("");
              setLabel("");
              setState("pending");
            }
          }}
        >
          <option value="">New dedicated route</option>
          {routes.map((route) => (
            <option key={route.id} value={route.id}>
              {String(route.value.label || route.id)}
            </option>
          ))}
        </select>
      </label>
      {incomplete && (
        <p className="notice">
          Load all configuration pages below before editing Discord scope. A
          later page may contain this resident's grant.
        </p>
      )}
      <form aria-label="Discord channel route" onSubmit={saveRoute}>
        <fieldset disabled={disabled || incomplete}>
          <legend>
            Channel binding · revision {selected?.value.revision ?? 0}
          </legend>
          <label>
            Local route ID
            <input
              required
              disabled={!!selected}
              maxLength={128}
              value={id}
              onChange={(e) => setId(e.target.value)}
            />
          </label>
          <label>
            Discord connection
            <select
              aria-label="Discord connection"
              required
              disabled={!!selected}
              value={connection}
              onChange={(e) => setConnection(e.target.value)}
            >
              <option value="">Choose a saved bot connection</option>
              {connections.map((item) => (
                <option key={item.id} value={item.id}>
                  {String(item.value.label || item.id)} ·{" "}
                  {String(item.value.state)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Resident for Discord
            <select
              aria-label="Resident for Discord"
              required
              disabled={!!selected}
              value={resident}
              onChange={(e) => setResident(e.target.value)}
            >
              <option value="">Choose an actual resident</option>
              {residents.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.id}
                </option>
              ))}
            </select>
          </label>
          {current && (
            <p>
              Resident ID {current.id} · runtime{" "}
              {runtimes
                ? runtimeLabel(runtimes, current.profile?.execution_profile)
                : (current.profile?.execution_profile ?? "Not reported")}{" "}
              · daily allowance ${(current.daily_limit / 1e6).toFixed(2)}.{" "}
              <a
                href={`#residents/${encodeURIComponent(current.id)}?tab=settings`}
              >
                Review resident settings
              </a>
            </p>
          )}
          <label>
            Discord guild ID
            <input
              required
              pattern="[0-9]+"
              maxLength={128}
              disabled={!!selected}
              value={guild}
              onChange={(e) => setGuild(e.target.value)}
            />
          </label>
          <label>
            Discord channel ID
            <input
              required
              pattern="[0-9]+"
              maxLength={128}
              disabled={!!selected}
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            />
          </label>
          <label>
            Channel display label
            <input
              maxLength={100}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </label>
          <label>
            Route state
            <select
              aria-label="Route state"
              value={state}
              onChange={(e) => setState(e.target.value)}
            >
              <option value="pending">Pending</option>
              <option value="active">Enabled</option>
              <option value="disabled">Disabled / revoked</option>
            </select>
          </label>
          <p>
            {selected
              ? `This route retains its saved ${words(String(selected.value.mode))} ownership and ${words(String(selected.value.sender_policy))} sender policy. Use the shared route editor below for an explicit policy change.`
              : "New routes use dedicated bot ownership and allow any human guild member who mentions the verified bot in this selected channel."}{" "}
            Bot, webhook, system and unmentioned messages cannot start work.
            These members do not become Hearth operators.
          </p>
          {selected && (
            <p>
              The source binding is immutable. Choose a new route to change its
              resident, connection or channel.
            </p>
          )}
          <button disabled={!validDestination}>Save Discord route</button>
          {selected && (
            <button
              type="button"
              disabled={selected.value.state === "disabled"}
              onClick={() => {
                const { revision, ...saved } = selected.value;
                void onSave("route", selected.id, revision, {
                  ...saved,
                  state: "disabled",
                });
              }}
            >
              Revoke Discord route
            </button>
          )}
        </fieldset>
      </form>
      <h4>3. Grant each capability separately</h4>
      <p>
        These permissions apply only to the selected connection, guild and
        channel. Other destinations retain their existing grants. Saving
        permissions does not enable the route or activate its connection.
      </p>
      {omittedGrant && (
        <p role="alert">
          This resident's existing grant exceeds the bounded editor. Use the
          owning CLI/API to inspect it; this form cannot overwrite it.
        </p>
      )}
      <form aria-label="Discord channel permissions" onSubmit={savePermissions}>
        <fieldset
          disabled={disabled || incomplete || omittedGrant || !validDestination}
        >
          <legend>
            Resident grant · revision {grant?.value.revision ?? 0}
          </legend>
          {powers.map(({ key, label, note }) => (
            <label key={key}>
              <input
                type="checkbox"
                disabled={
                  !permission[key] &&
                  ((grant?.value[key] as Scope[] | undefined)?.length ?? 0) >=
                    32
                }
                checked={permission[key] ?? false}
                onChange={(e) =>
                  setPermission({ ...permission, [key]: e.target.checked })
                }
              />
              {label}
              <small>{note}</small>
            </label>
          ))}
          {permission.listen && !permission.reply && (
            <p className="notice">
              Mention requests cannot run without reply permission for this
              channel.
            </p>
          )}
          <button>Save Discord permissions</button>
          <button
            type="button"
            onClick={() => {
              const value = Object.fromEntries(
                powers.map(({ key }) => [
                  key,
                  ((grant?.value[key] as Scope[] | undefined) ?? []).filter(
                    (scope) => !same(scope, destination),
                  ),
                ]),
              );
              void onSave("grant", resident, grant?.value.revision ?? 0, value);
            }}
          >
            Revoke selected channel permissions
          </button>
        </fieldset>
      </form>
      <h4>Polling and delivery</h4>
      <p>
        Discord uses bounded REST polling. The bot may appear offline in
        Discord; that presence does not establish worker failure. Replies have
        polling and task-execution latency. An empty history result does not
        prove that content access or channel permissions are available.
      </p>
      <p>
        Run success, delivery confirmation and unknown external effects remain
        separate in Conversations, Announcements and Notification deliveries.{" "}
        <a href="#inbox">Inbox</a> read status and resident Letters remain
        independent.
      </p>
      <DiscordDiagnostics configs={configs} status={status} />
    </details>
  );
}
const diagnostics: Record<string, string> = {
  authentication_failed:
    "Discord rejected the token. Replace it in the protected server slot; never paste it here.",
  permission_denied:
    "Discord denied channel permissions. Review View Channel, Read Message History, Send Messages and channel overrides.",
  communications_secret_invalid:
    "The protected credential is invalid or has unsafe storage permissions.",
  communications_secret_reference_invalid:
    "The protected credential reference is invalid.",
  communications_secret_changed:
    "The protected credential changed or disappeared; this client cannot continue with its old credential.",
  communications_pending:
    "The connection is pending; credential readiness has not been established.",
  rate_limited:
    "Discord rate limit recorded. The worker waits for the full recorded deadline.",
  unavailable:
    "The transport is unreachable or unavailable; channel contents are not known.",
  disconnected: "The local transport client is disconnected.",
  ready:
    "Last recorded transport check succeeded; this is cached evidence, not a live probe.",
  communications_route_inactive: "The route is inactive or revoked.",
  communications_scope_denied:
    "The required channel capability is not granted.",
};
export function DiscordDiagnostics({
  configs,
  status,
}: {
  configs: Config[];
  status: Status;
}) {
  const credentials = status.health.credentials as
    | Record<string, { state: string; checked_at: number; revision: number }>
    | undefined;
  const health = status.health.connections as
    Record<string, { state?: string; content_access?: string }> | undefined;
  return (
    <section aria-label="Discord diagnostics">
      <h4>Discord diagnostics · cached evidence</h4>
      {configs
        .filter(
          (item) =>
            item.kind === "connection" && item.value.transport === "discord",
        )
        .map((connection) => {
          const evidence = health?.[connection.id];
          const recordedCredential = credentials?.[connection.id];
          const credential =
            recordedCredential?.revision === connection.value.revision
              ? recordedCredential
              : undefined;
          const progress = (status.poll_progress ?? []).filter(
            (item) => item.connection_id === connection.id,
          );
          const routes = configs.filter(
            (item) =>
              item.kind === "route" &&
              item.value.connection_id === connection.id,
          );
          const schedules = status.schedule.filter(
            (item) =>
              (item.kind === "connection" && item.id === connection.id) ||
              (item.kind === "route" &&
                routes.some((route) => route.id === item.id)),
          );
          return (
            <article key={connection.id}>
              <h5>{String(connection.value.label || connection.id)}</h5>
              <p>
                Connection {words(String(connection.value.state))} · worker{" "}
                {words(String(status.health.communications ?? "unknown"))}
              </p>
              <p>
                Protected token:{" "}
                {credential
                  ? ({
                      missing: "missing at the last worker check",
                      invalid: "invalid or unsafe at the last worker check",
                      configured:
                        "configured and readable; Discord authentication is not proved",
                    }[credential.state] ?? "unknown")
                  : "unknown — no cached check for this configuration revision"}
                {credential && ` · checked ${time(credential.checked_at)}`}
              </p>
              <p>
                {evidence?.state
                  ? (diagnostics[evidence.state] ??
                    `Recorded transport state: ${words(evidence.state)}`)
                  : "No transport check recorded. Pending configuration does not distinguish a missing token from a token that has not been checked."}
              </p>
              <p>
                Message Content access: {evidence?.content_access ?? "unknown"}
                {evidence?.content_access === "unavailable"
                  ? " — general history text is unavailable; successful mentions do not prove history access."
                  : ""}
              </p>
              {routes.map((route) => (
                <p key={route.id}>
                  Route {String(route.value.label || route.id)} ·{" "}
                  {route.value.state === "disabled"
                    ? "Revoked"
                    : words(String(route.value.state))}
                </p>
              ))}
              {!progress.length && <p>No polling progress recorded.</p>}
              {progress.map((item) => (
                <p key={`${item.guild_id}:${item.channel_id}`}>
                  Channel {item.channel_id} · last successful baseline or scan
                  progress {time(item.updated_at)} ·{" "}
                  {item.through_id === null
                    ? "No unfinished polling window recorded"
                    : "Backlog scan in progress"}
                  . This timestamp is not a live heartbeat.
                </p>
              ))}
              {schedules.map((item) => (
                <p key={`${item.kind}:${item.id}`}>
                  {item.error
                    ? (diagnostics[item.error] ?? words(item.error))
                    : "Scheduled retry"}{" "}
                  · eligible {time(item.eligible_at)}
                </p>
              ))}
            </article>
          );
        })}
    </section>
  );
}
