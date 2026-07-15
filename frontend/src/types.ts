export interface Media {
  id: number;
  type: "image" | "video";
  source_url: string;
  video_url: string | null;
  local_path: string | null;
  poster_path: string | null;
  width: number | null;
  height: number | null;
  duration: number | null;
  mime: string | null;
  analysis_ru: string | null;
}

export interface SourceBrief {
  id: number;
  name: string;
  type: string;
  lang: string;
}

export interface Item {
  id: number;
  url: string | null;
  orig_title: string;
  title_ru: string | null;
  summary_ru: string | null;
  category: string | null;
  published_at: string | null;
  source: SourceBrief | null;
  media: Media[];
}

export interface Cluster {
  id: number;
  headline_ru: string;
  digest_ru: string;
  key_points: string[] | null;
  category: string;
  event_type: string | null;
  first_seen: string;
  last_updated: string;
  source_count: number;
  lat: number | null;
  lon: number | null;
  place_name: string | null;
  admin1: string | null;
  admin2: string | null;
  country: string | null;
  geo_confidence: number;
  geo_needs_review: boolean;
  primary_media: Media | null;
  sources: SourceBrief[];
  items: Item[];
}

export interface EventProps {
  id: number;
  headline_ru: string;
  digest_ru: string;
  event_type: string | null;
  category: string;
  place_name: string | null;
  admin1: string | null;
  admin2: string | null;
  country: string | null;
  time: string | null;
  source_count: number;
  geo_confidence: number;
  geo_needs_review: boolean;
  media: { type: string; url: string | null; poster: string | null; video_url: string | null } | null;
  "marker-color": string;
}

export interface EventFeature {
  type: "Feature";
  geometry: { type: "Point"; coordinates: [number, number] };
  properties: EventProps;
}

export interface EventCollection {
  type: "FeatureCollection";
  features: EventFeature[];
}

export interface Stats {
  total: number;
  geolocated: number;
  by_type: { key: string; count: number }[];
  by_region: { key: string; count: number }[];
  by_day: { day: string; count: number }[];
}

export interface FeedResponse {
  total: number;
  limit: number;
  offset: number;
  clusters: Cluster[];
}

export interface Status {
  summarizer_backend: string;
  summarizer_model: string;
  summarizer_available: boolean;
  vision_backend: string;
  vision_model: string;
  vision_available: boolean;
  source_mode: string;
  last_ingest: string | null;
  clusters: number;
  items: number;
  sources: number;
}

export interface SourceFull extends SourceBrief {
  url_or_username: string;
  category_hint: string | null;
  enabled: boolean;
  last_fetch: string | null;
}

export const CATEGORIES = [
  "Фронт/боевые действия",
  "Дипломатия/переговоры",
  "Санкции",
  "Военная помощь/поставки",
  "Внутренняя политика РФ",
  "Западная коалиция/НАТО",
  "Прочее",
];

export const EVENT_TYPES = [
  "удар_ракетный",
  "удар_дрон",
  "удар_авиа",
  "обстрел",
  "работа_ПВО",
  "бои_наступление",
  "потеря_техники",
  "поставки_вооружений",
  "дипломатия",
  "санкции",
  "инфраструктура_ЧП",
  "прочее",
];

export const EVENT_COLORS: Record<string, string> = {
  удар_ракетный: "#e0563c",
  удар_дрон: "#e0863c",
  удар_авиа: "#d64541",
  обстрел: "#c0392b",
  работа_ПВО: "#3fb0e0",
  бои_наступление: "#9b59b6",
  потеря_техники: "#8e6e53",
  поставки_вооружений: "#27ae60",
  дипломатия: "#2980b9",
  санкции: "#f1c40f",
  инфраструктура_ЧП: "#e67e22",
  прочее: "#95a5a6",
};

export function eventColor(t: string | null): string {
  return (t && EVENT_COLORS[t]) || EVENT_COLORS["прочее"];
}

export function eventLabel(t: string | null): string {
  return (t || "прочее").replace(/_/g, " ");
}
