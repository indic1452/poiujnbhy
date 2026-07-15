import { useCallback, useEffect, useState } from "react";
import { fetchFeed, fetchSources } from "../api/client";
import type { Cluster, SourceFull } from "../types";
import { CategoryTabs } from "./CategoryTabs";
import { Feed } from "./Feed";
import { SearchBox, SourceFilter } from "./Controls";

export function FeedView({ reloadKey }: { reloadKey: number }) {
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [sources, setSources] = useState<SourceFull[]>([]);
  const [category, setCategory] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [sourceId, setSourceId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchFeed({
        category: category ?? undefined,
        q: q || undefined,
        source_id: sourceId ?? undefined,
        limit: 50,
      });
      setClusters(data.clusters);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
    } finally {
      setLoading(false);
    }
  }, [category, q, sourceId]);

  useEffect(() => {
    fetchSources().then(setSources).catch(() => undefined);
  }, []);

  useEffect(() => {
    const t = setTimeout(load, q ? 300 : 0);
    return () => clearTimeout(t);
  }, [load, q, reloadKey]);

  return (
    <>
      <div className="toolbar">
        <SearchBox value={q} onChange={setQ} />
        <SourceFilter sources={sources} value={sourceId} onChange={setSourceId} />
      </div>
      <CategoryTabs value={category} onChange={setCategory} />
      <Feed clusters={clusters} loading={loading} error={error} />
    </>
  );
}
