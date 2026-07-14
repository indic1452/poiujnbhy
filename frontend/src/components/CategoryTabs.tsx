import { CATEGORIES } from "../types";

export function CategoryTabs({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (c: string | null) => void;
}) {
  return (
    <div className="tabs">
      <button
        className={`tab ${value === null ? "active" : ""}`}
        onClick={() => onChange(null)}
      >
        Все
      </button>
      {CATEGORIES.map((c) => (
        <button
          key={c}
          className={`tab ${value === c ? "active" : ""}`}
          onClick={() => onChange(c)}
        >
          {c}
        </button>
      ))}
    </div>
  );
}
