import type { Cluster } from "../types";
import { ClusterCard } from "./ClusterCard";

export function Feed({
  clusters,
  loading,
  error,
}: {
  clusters: Cluster[];
  loading: boolean;
  error: string | null;
}) {
  if (error) return <div className="notice error">{error}</div>;
  if (loading && clusters.length === 0)
    return <div className="notice">Загрузка ленты…</div>;
  if (!loading && clusters.length === 0)
    return (
      <div className="notice">
        Пока нет сюжетов. Нажмите «Обновить», чтобы опросить источники.
      </div>
    );
  return (
    <div className="feed">
      {clusters.map((c) => (
        <ClusterCard key={c.id} cluster={c} />
      ))}
    </div>
  );
}
