import { useEffect, useMemo, useState } from "react";
import { MapContainer, Marker, TileLayer } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { fetchEvents, mediaSrc } from "../api/client";
import type { EventFeature } from "../types";
import { EVENT_TYPES, eventColor, eventLabel } from "../types";
import { formatDate } from "../utils";
import { sinceFromPeriod } from "../utils";

const TILE_URL =
  import.meta.env.VITE_TILE_URL || "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTR = "© OpenStreetMap contributors";

function markerIcon(f: EventFeature): L.DivIcon {
  const color = f.properties["marker-color"] || eventColor(f.properties.event_type);
  const media = f.properties.media;
  const img = media && (media.type === "image" || media.poster) ? mediaSrc(media.url) : "";
  if (img) {
    return L.divIcon({
      className: "",
      html: `<div class="pin-photo" style="border-color:${color}"><img src="${img}" loading="lazy"/></div>`,
      iconSize: [44, 44],
      iconAnchor: [22, 22],
    });
  }
  return L.divIcon({
    className: "",
    html: `<div class="pin-dot" style="background:${color}"></div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function clusterIcon(cluster: any): L.DivIcon {
  const n = cluster.getChildCount();
  return L.divIcon({
    html: `<div class="cluster-bubble">${n}</div>`,
    className: "",
    iconSize: [40, 40],
  });
}

function EventPanel({ f, onClose }: { f: EventFeature; onClose: () => void }) {
  const p = f.properties;
  const [lon, lat] = f.geometry.coordinates;
  const media = p.media;
  const color = eventColor(p.event_type);
  return (
    <aside className="event-panel">
      <button className="panel-close" onClick={onClose}>
        ✕
      </button>
      <div className="panel-tags">
        <span className="chip" style={{ borderColor: color, color }}>
          {eventLabel(p.event_type)}
        </span>
        {p.geo_needs_review && <span className="review-badge">требует проверки</span>}
      </div>
      <h3>{p.headline_ru}</h3>
      <div className="panel-meta">
        📍 {p.place_name || "—"}
        {p.admin1 ? `, ${p.admin1}` : ""} · {formatDate(p.time)}
      </div>
      {media && (media.url || media.poster) && (
        <div className="panel-media">
          {media.type === "video" && media.video_url ? (
            <a href={media.video_url} target="_blank" rel="noreferrer">
              <img src={mediaSrc(media.poster || media.url)} alt="" />
              <span className="play-badge">▶ Видео</span>
            </a>
          ) : (
            <img src={mediaSrc(media.url)} alt="" />
          )}
        </div>
      )}
      <p className="panel-digest">{p.digest_ru}</p>
      <div className="panel-coords">
        {lat.toFixed(4)}, {lon.toFixed(4)} · источников: {p.source_count} · уверенность{" "}
        {(p.geo_confidence * 100).toFixed(0)}%
      </div>
    </aside>
  );
}

export function MapView({ reloadKey }: { reloadKey: number }) {
  const [features, setFeatures] = useState<EventFeature[]>([]);
  const [selected, setSelected] = useState<EventFeature | null>(null);
  const [period, setPeriod] = useState("all");
  const [etype, setEtype] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchEvents({ since: sinceFromPeriod(period), event_type: etype || undefined })
      .then((c) => {
        setFeatures(c.features);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
  }, [period, etype, reloadKey]);

  const markers = useMemo(
    () =>
      features.map((f) => (
        <Marker
          key={f.properties.id}
          position={[f.geometry.coordinates[1], f.geometry.coordinates[0]]}
          icon={markerIcon(f)}
          eventHandlers={{ click: () => setSelected(f) }}
        />
      )),
    [features]
  );

  return (
    <div className="mapview">
      <div className="toolbar">
        <select className="source-select" value={period} onChange={(e) => setPeriod(e.target.value)}>
          <option value="all">Весь период</option>
          <option value="24h">24 часа</option>
          <option value="7d">7 дней</option>
          <option value="30d">30 дней</option>
        </select>
        <select className="source-select" value={etype} onChange={(e) => setEtype(e.target.value)}>
          <option value="">Все типы событий</option>
          {EVENT_TYPES.map((t) => (
            <option key={t} value={t}>
              {eventLabel(t)}
            </option>
          ))}
        </select>
        <span className="map-count">событий на карте: {features.length}</span>
      </div>
      {error && <div className="notice error">{error}</div>}
      <div className="map-wrap">
        <MapContainer center={[48.5, 36]} zoom={6} className="map-container" scrollWheelZoom>
          <TileLayer url={TILE_URL} attribution={TILE_ATTR} />
          <MarkerClusterGroup iconCreateFunction={clusterIcon} chunkedLoading>
            {markers}
          </MarkerClusterGroup>
        </MapContainer>
        {selected && <EventPanel f={selected} onClose={() => setSelected(null)} />}
      </div>
    </div>
  );
}
