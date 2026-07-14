import { useState } from "react";
import type { SourceFull } from "../types";

export function SearchBox({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <input
      className="search"
      type="search"
      placeholder="Поиск по сводкам…"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function SourceFilter({
  sources,
  value,
  onChange,
}: {
  sources: SourceFull[];
  value: number | null;
  onChange: (id: number | null) => void;
}) {
  return (
    <select
      className="source-select"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
    >
      <option value="">Все источники</option>
      {sources.map((s) => (
        <option key={s.id} value={s.id}>
          {s.name}
        </option>
      ))}
    </select>
  );
}

export function RefreshButton({ onRefresh }: { onRefresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      className="refresh"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await onRefresh();
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? "Обновление…" : "⟳ Обновить"}
    </button>
  );
}
