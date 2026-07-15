import type { EventCollection, FeedResponse, SourceFull, Stats, Status } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export function mediaSrc(path: string | null): string {
  if (!path) return "";
  if (path.startsWith("http")) return path;
  return `${API_BASE}${path}`;
}

export interface FeedParams {
  category?: string;
  q?: string;
  source_id?: number;
  limit?: number;
  offset?: number;
}

export async function fetchFeed(params: FeedParams = {}): Promise<FeedResponse> {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  if (params.q) qs.set("q", params.q);
  if (params.source_id) qs.set("source_id", String(params.source_id));
  qs.set("limit", String(params.limit ?? 30));
  qs.set("offset", String(params.offset ?? 0));
  const r = await fetch(`${API_BASE}/api/feed?${qs.toString()}`);
  if (!r.ok) throw new Error(`Ошибка загрузки ленты: ${r.status}`);
  return r.json();
}

export async function fetchStatus(): Promise<Status> {
  const r = await fetch(`${API_BASE}/api/status`);
  if (!r.ok) throw new Error(`Ошибка статуса: ${r.status}`);
  return r.json();
}

export async function fetchSources(): Promise<SourceFull[]> {
  const r = await fetch(`${API_BASE}/api/sources`);
  if (!r.ok) throw new Error(`Ошибка источников: ${r.status}`);
  return r.json();
}

export async function triggerRefresh(): Promise<{ started: boolean; detail: string }> {
  const r = await fetch(`${API_BASE}/api/refresh`, { method: "POST" });
  if (!r.ok) throw new Error(`Ошибка обновления: ${r.status}`);
  return r.json();
}

export interface EventFilters {
  since?: string;
  event_type?: string;
  region?: string;
}

function eventQuery(f: EventFilters): string {
  const qs = new URLSearchParams();
  if (f.since) qs.set("since", f.since);
  if (f.event_type) qs.set("event_type", f.event_type);
  if (f.region) qs.set("region", f.region);
  return qs.toString();
}

export async function fetchEvents(f: EventFilters = {}): Promise<EventCollection> {
  const r = await fetch(`${API_BASE}/api/events.geojson?${eventQuery(f)}`);
  if (!r.ok) throw new Error(`Ошибка карты: ${r.status}`);
  return r.json();
}

export async function fetchStats(f: EventFilters = {}): Promise<Stats> {
  const r = await fetch(`${API_BASE}/api/stats?${eventQuery(f)}`);
  if (!r.ok) throw new Error(`Ошибка статистики: ${r.status}`);
  return r.json();
}

export function exportUrl(format: "geojson" | "csv" | "json", f: EventFilters = {}): string {
  const qs = eventQuery(f);
  return `${API_BASE}/api/export?format=${format}${qs ? "&" + qs : ""}`;
}
