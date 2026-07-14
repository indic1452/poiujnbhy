import type { Status } from "../types";
import { formatDate } from "../utils";

export function StatusBar({ status }: { status: Status | null }) {
  if (!status) return null;
  const engine = (backend: string, model: string, ok: boolean) => (
    <span className={`engine ${ok ? "ok" : "down"}`}>
      {backend === "mock" ? "mock" : `${backend}:${model}`}
    </span>
  );
  return (
    <div className="statusbar">
      <span>
        Текст: {engine(status.summarizer_backend, status.summarizer_model, status.summarizer_available)}
      </span>
      <span>
        Зрение: {engine(status.vision_backend, status.vision_model, status.vision_available)}
      </span>
      <span>Режим: {status.source_mode}</span>
      <span>Сюжетов: {status.clusters}</span>
      <span>Материалов: {status.items}</span>
      <span>Обновлено: {formatDate(status.last_ingest) || "—"}</span>
    </div>
  );
}
