import { Client } from "../../shared/client";
export type Scope = {
  connection_id: string;
  guild_id?: string;
  channel_id?: string;
  target_id?: string;
};
export type Config = {
  kind: string;
  id: string;
  value: Record<string, unknown> & { revision: number };
};
export type Status = {
  read_only: boolean;
  configuration: (
    | Config
    | {
        kind: string;
        id: string;
        value: null;
        revision: number;
        omitted: string;
      }
  )[];
  next_after: string | null;
  retention: string;
  bindings: { connection_id: string; revision: number }[];
  schedule: {
    kind: string;
    id: string;
    eligible_at: number;
    error: string | null;
  }[];
  health: Record<string, unknown>;
  poll_progress?: {
    connection_id: string;
    guild_id: string;
    channel_id: string;
    cursor: string;
    through_id: string | null;
    updated_at: number;
  }[];
};
export type Conversation = {
  id: string;
  channel_label: string;
  resident_id: string;
  connection_id: string;
  channel_id: string;
  sender_id: string;
  busy: number;
  last_at: number;
};
export type Turn = {
  id: string;
  direction: string;
  created_at: number;
  text: string | null;
  reply: string | null;
  task_id: string | null;
  run_id: string | null;
  run_status: string | null;
  state: string;
  reason: string | null;
  operation_id: string | null;
  delivery_state: string | null;
  usage_known: number | null;
  actual_cost: number | null;
  omission?: string;
};
export type Transcript = {
  conversation: Conversation;
  route: { label: string; address: Scope };
  turns: Turn[];
  next_before: string | null;
  dropped_in_channel: { reason: string; count: number }[];
  retention: string;
  read_only: boolean;
};
export type Operation = {
  id: string;
  kind: string;
  state: string;
  revision: number;
  source_id: string;
  run_id: string | null;
  task_id: string | null;
  created_at: number;
  eligible_at: number;
  parent_id: string | null;
};
export type Delivery = Operation & {
  uncertain_attempt_ids?: string[];
  current_authority?: { allowed: boolean; reason: string | null };
  latest_reason?: { kind: string; at: number; reason: string } | null;
  notification?: {
    kind: string;
    resource_id: string;
    read_at: number | null;
  } | null;
  text: string;
  sha256: string;
  direction: string;
  destination: Scope;
  routine_id: string | null;
  actions: string[];
  read_only: boolean;
  run: {
    status: string;
    usage_known: number;
    actual_cost: number | null;
  } | null;
  attempts: {
    id: string;
    state: string;
    dispatched_at: number | null;
    completed_at: number | null;
    external_id: string | null;
    evidence: string | null;
  }[];
  resolutions: {
    revision: number;
    action: string;
    at: number;
    reason: string;
    evidence: unknown;
  }[];
  resolution_count: number;
};
export type Forwarding = {
  id: string;
  revision: number;
  destination: Scope;
  kinds: string[];
  enabled: number | boolean;
  operator_url: string | null;
  cursor: number;
  activated_after: number;
};
export type Usage = {
  origins: {
    root_task_id: string;
    origin: string;
    known_cost: number;
    unknown_runs: number;
    active_runs?: number;
    reserved?: number;
    runs: number;
  }[];
  truncated: boolean;
};
export function api(client: Client) {
  const get = <T>(path: string) =>
    client.request<T>(`/api/communications${path}`);
  const write = (path: string, body: unknown, method = "POST") =>
    client.request<unknown>(`/api/communications${path}`, {
      method,
      body: JSON.stringify(body),
    });
  return { get, write };
}
export const time = (value: number | null | undefined) =>
  value == null ? "Not recorded" : new Date(value * 1000).toLocaleString();
export const words = (value: string) => value.replaceAll("_", " ");
export const cost = (known: number | null, amount: number | null) =>
  known
    ? amount == null
      ? "Usage known; cost unavailable"
      : `$${(amount / 1e6).toFixed(4)} known usage`
    : "Usage unknown";
