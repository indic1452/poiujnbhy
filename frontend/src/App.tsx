import { useCallback, useEffect, useState } from "react";
import { fetchStatus, triggerRefresh } from "./api/client";
import type { Status } from "./types";
import { Header } from "./components/Header";
import { StatusBar } from "./components/StatusBar";
import { RefreshButton } from "./components/Controls";
import { FeedView } from "./components/FeedView";
import { MapView } from "./components/MapView";
import { StatsView } from "./components/StatsView";

type View = "feed" | "map" | "stats";

const TABS: { k: View; label: string }[] = [
  { k: "feed", label: "Лента" },
  { k: "map", label: "Карта" },
  { k: "stats", label: "Статистика" },
];

export default function App() {
  const [view, setView] = useState<View>("feed");
  const [status, setStatus] = useState<Status | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await fetchStatus());
    } catch {
      /* статус не критичен */
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const onRefresh = useCallback(async () => {
    try {
      await triggerRefresh();
    } catch {
      /* покажем текущие данные */
    }
    await new Promise((r) => setTimeout(r, 1800));
    setReloadKey((k) => k + 1);
    await loadStatus();
  }, [loadStatus]);

  return (
    <div className="app">
      <Header />
      <StatusBar status={status} />
      <div className="viewbar">
        <div className="viewtabs">
          {TABS.map((t) => (
            <button
              key={t.k}
              className={`vtab ${view === t.k ? "active" : ""}`}
              onClick={() => setView(t.k)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <RefreshButton onRefresh={onRefresh} />
      </div>

      {view === "feed" && <FeedView reloadKey={reloadKey} />}
      {view === "map" && <MapView reloadKey={reloadKey} />}
      {view === "stats" && <StatsView reloadKey={reloadKey} />}

      <footer className="app-foot">
        Локальный ИИ · без облака · агрегатор открытых источников · гео © GeoNames (CC BY 4.0),
        © OpenStreetMap
      </footer>
    </div>
  );
}
