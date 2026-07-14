import { useCallback, useEffect, useState } from "react";
import { fetchFeed, fetchSources, fetchStatus, triggerRefresh } from "./api/client";
import type { Cluster, SourceFull, Status } from "./types";
import { Header } from "./components/Header";
import { StatusBar } from "./components/StatusBar";
import { CategoryTabs } from "./components/CategoryTabs";
import { Feed } from "./components/Feed";
import { RefreshButton, SearchBox, SourceFilter } from "./components/Controls";

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [sources, setSources] = useState<SourceFull[]>([]);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [category, setCategory] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [sourceId, setSourceId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadFeed = useCallback(async () => {
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

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await fetchStatus());
    } catch {
      /* статус не критичен */
    }
  }, []);

  useEffect(() => {
    fetchSources().then(setSources).catch(() => undefined);
    loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    const t = setTimeout(loadFeed, q ? 300 : 0);
    return () => clearTimeout(t);
  }, [loadFeed, q]);

  const onRefresh = useCallback(async () => {
    try {
      await triggerRefresh();
    } catch {
      /* игнорируем — покажем текущую ленту */
    }
    await new Promise((r) => setTimeout(r, 1800));
    await Promise.all([loadFeed(), loadStatus()]);
  }, [loadFeed, loadStatus]);

  return (
    <div className="app">
      <Header />
      <StatusBar status={status} />
      <div className="toolbar">
        <SearchBox value={q} onChange={setQ} />
        <SourceFilter sources={sources} value={sourceId} onChange={setSourceId} />
        <RefreshButton onRefresh={onRefresh} />
      </div>
      <CategoryTabs value={category} onChange={setCategory} />
      <Feed clusters={clusters} loading={loading} error={error} />
      <footer className="app-foot">
        Локальный ИИ · без облака · агрегатор открытых источников
      </footer>
    </div>
  );
}
