import { useState } from "react";
import type { Cluster } from "../types";
import { formatDate, langBadge } from "../utils";
import { MediaGallery } from "./MediaGallery";

export function ClusterCard({ cluster }: { cluster: Cluster }) {
  const [open, setOpen] = useState(false);
  const media = cluster.primary_media;

  return (
    <article className="card">
      <div className="card-top">
        <span className="chip">{cluster.category}</span>
        <span className="time">{formatDate(cluster.last_updated)}</span>
      </div>

      <h2 className="headline">{cluster.headline_ru}</h2>

      {media && (
        <figure className="media-figure">
          <MediaGallery media={media} />
          {media.analysis_ru && (
            <figcaption className="media-caption">
              <span className="ai-tag">анализ ИИ</span> {media.analysis_ru}
            </figcaption>
          )}
        </figure>
      )}

      <p className="digest">{cluster.digest_ru}</p>

      {cluster.key_points && cluster.key_points.length > 0 && (
        <ul className="points">
          {cluster.key_points.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      )}

      <div className="card-footer">
        <div className="sources">
          {cluster.sources.map((s) => (
            <span key={s.id} className="source-badge" title={s.type}>
              <span className="lang">{langBadge(s.lang)}</span> {s.name}
            </span>
          ))}
        </div>
        {cluster.items.length > 1 && (
          <button className="link-btn" onClick={() => setOpen((v) => !v)}>
            {open ? "Скрыть источники" : `Источники (${cluster.items.length})`}
          </button>
        )}
      </div>

      {open && (
        <div className="items">
          {cluster.items.map((it) => (
            <div key={it.id} className="item-row">
              <div className="item-main">
                <div className="item-title">
                  {it.source && (
                    <span className="lang small">{langBadge(it.source.lang)}</span>
                  )}
                  {it.url ? (
                    <a href={it.url} target="_blank" rel="noreferrer">
                      {it.title_ru || it.orig_title}
                    </a>
                  ) : (
                    <span>{it.title_ru || it.orig_title}</span>
                  )}
                </div>
                {it.summary_ru && <p className="item-summary">{it.summary_ru}</p>}
                <div className="item-meta">
                  {it.source?.name} · {formatDate(it.published_at)}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}
