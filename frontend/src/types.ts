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
  first_seen: string;
  last_updated: string;
  source_count: number;
  primary_media: Media | null;
  sources: SourceBrief[];
  items: Item[];
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
