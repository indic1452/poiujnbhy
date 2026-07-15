import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { exportUrl, fetchStats } from "../api/client";
import type { Stats } from "../types";
import { eventColor, eventLabel } from "../types";
import { sinceFromPeriod } from "../utils";

const AXIS = { fill: "#9aa4b2", fontSize: 11 };
const GRID = "#2a313c";

export function StatsView({ reloadKey }: { reloadKey: number }) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [period, setPeriod] = useState("all");
  const [error, setError] = useState<string | null>(null);

  const since = sinceFromPeriod(period);

  useEffect(() => {
    fetchStats({ since })
      .then((s) => {
        setStats(s);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
  }, [since, reloadKey]);

  if (error) return <div className="notice error">{error}</div>;
  if (!stats) return <div className="notice">Загрузка статистики…</div>;

  const byType = stats.by_type.map((d) => ({ ...d, label: eventLabel(d.key) }));

  return (
    <div className="statsview">
      <div className="toolbar">
        <select className="source-select" value={period} onChange={(e) => setPeriod(e.target.value)}>
          <option value="all">Весь период</option>
          <option value="24h">24 часа</option>
          <option value="7d">7 дней</option>
          <option value="30d">30 дней</option>
        </select>
        <div className="export-btns">
          <a className="export-link" href={exportUrl("geojson", { since })}>
            ⬇ GeoJSON
          </a>
          <a className="export-link" href={exportUrl("csv", { since })}>
            ⬇ CSV
          </a>
          <a className="export-link" href={exportUrl("json", { since })}>
            ⬇ JSON
          </a>
        </div>
      </div>

      <div className="stat-cards">
        <div className="stat-card">
          <div className="stat-num">{stats.total}</div>
          <div className="stat-lbl">событий</div>
        </div>
        <div className="stat-card">
          <div className="stat-num">{stats.geolocated}</div>
          <div className="stat-lbl">на карте</div>
        </div>
        <div className="stat-card">
          <div className="stat-num">{stats.by_region.length}</div>
          <div className="stat-lbl">районов</div>
        </div>
      </div>

      <div className="chart-block">
        <h3>События по типам</h3>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={byType} layout="vertical" margin={{ left: 40 }}>
            <CartesianGrid stroke={GRID} horizontal={false} />
            <XAxis type="number" tick={AXIS} stroke={GRID} allowDecimals={false} />
            <YAxis type="category" dataKey="label" tick={AXIS} stroke={GRID} width={120} />
            <Tooltip contentStyle={{ background: "#171b22", border: `1px solid ${GRID}` }} />
            <Bar dataKey="count" radius={[0, 4, 4, 0]} isAnimationActive={false}>
              {byType.map((d) => (
                <Cell key={d.key} fill={eventColor(d.key)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="chart-block">
        <h3>События по районам</h3>
        <ResponsiveContainer width="100%" height={Math.max(160, stats.by_region.length * 26)}>
          <BarChart data={stats.by_region} layout="vertical" margin={{ left: 60 }}>
            <CartesianGrid stroke={GRID} horizontal={false} />
            <XAxis type="number" tick={AXIS} stroke={GRID} allowDecimals={false} />
            <YAxis type="category" dataKey="key" tick={AXIS} stroke={GRID} width={150} />
            <Tooltip contentStyle={{ background: "#171b22", border: `1px solid ${GRID}` }} />
            <Bar dataKey="count" fill="#e0563c" radius={[0, 4, 4, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="chart-block">
        <h3>Динамика по дням</h3>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={stats.by_day}>
            <CartesianGrid stroke={GRID} />
            <XAxis dataKey="day" tick={AXIS} stroke={GRID} />
            <YAxis tick={AXIS} stroke={GRID} allowDecimals={false} />
            <Tooltip contentStyle={{ background: "#171b22", border: `1px solid ${GRID}` }} />
            <Line type="monotone" dataKey="count" stroke="#e0563c" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
