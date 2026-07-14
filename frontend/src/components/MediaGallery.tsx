import type { Media } from "../types";
import { mediaSrc } from "../api/client";
import { formatDuration } from "../utils";

export function MediaGallery({ media }: { media: Media }) {
  if (media.type === "video") {
    const poster = mediaSrc(media.poster_path);
    if (media.local_path) {
      return (
        <video
          className="media"
          controls
          poster={poster || undefined}
          src={mediaSrc(media.local_path)}
        />
      );
    }
    return (
      <div className="media-video-ext">
        {poster ? (
          <img className="media" src={poster} alt="" loading="lazy" />
        ) : (
          <div className="media media-placeholder">видео</div>
        )}
        {media.video_url && (
          <a
            className="play-badge"
            href={media.video_url}
            target="_blank"
            rel="noreferrer"
          >
            ▶ Смотреть видео
            {media.duration ? ` · ${formatDuration(media.duration)}` : ""}
          </a>
        )}
      </div>
    );
  }

  const img = mediaSrc(media.local_path) || mediaSrc(media.source_url);
  if (!img) return null;
  return <img className="media" src={img} alt="" loading="lazy" />;
}
