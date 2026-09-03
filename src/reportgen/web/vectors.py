"""Векторы библиотеки: построение в фоне и честное состояние поиска.

Смысловой поиск (плотный канал в :mod:`reportgen.search`) работает только
по тем фрагментам, для которых построены векторы. Строила их одна команда
из консоли — ``reportgen embed``. На изолированной машине к консоли никто
не подходит: инженер кладёт книгу через «Библиотеку», книга ложится в базу,
находится словами — и не находится по смыслу. Узнать об этом было неоткуда:
единственный признак стоял серой строкой под ответом помощника.

Здесь два дела. Первое — построить векторы сразу после того, как документ
попал в библиотеку, не заставляя человека ждать в браузере: на книге в
триста страниц это минуты работы видеокарты. Второе — уметь ответить на
вопрос «а смысловой поиск вообще работает?» числами: сколько фрагментов,
сколько из них с векторами, той ли моделью они построены, отвечает ли
служба.

Работа идёт в одном потоке на приложение: две одновременные постройки
поделили бы видеокарту и обе шли бы вдвое дольше. Повторный запуск при
идущей работе — не ошибка, а «уже строим».
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Dict

from ..embeddings import EmbeddingClient, EmbeddingError, index_embeddings

if TYPE_CHECKING:  # pragma: no cover — только для подсказок типов
    from ..config import Settings
    from ..store.repo import Repositories

__all__ = ["VectorIndexer"]

#: Столько секунд держим последний посчитанный статус. Считается он двумя
#: запросами к SQLite, но экран библиотеки открывают часто, а построение
#: векторов и без того занимает базу.
STATUS_TTL = 2.0

#: Сколько проходов построения делаем подряд. Библиотека растёт на ходу:
#: пока идёт долгая книга, кладут вторую. Больше трёх проходов — признак
#: того, что дело не в новых документах.
_MAX_PASSES = 3


class VectorIndexer:
    """Построение векторов библиотеки и состояние смыслового поиска."""

    def __init__(self, repos: "Repositories", settings: "Settings",
                 client_factory: "Callable[[], Any] | None" = None):
        self.repos = repos
        self.settings = settings
        #: Чем строить векторы. Подменяется в тестах: поднимать рядом сервер
        #: эмбеддингов ради проверки логики очереди никто не станет.
        self.client_factory = client_factory or self._default_client
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._done = 0
        self._total = 0
        self._error = ""
        self._finished_at = 0.0
        self._written = 0
        self._status: Dict[str, Any] | None = None
        self._status_at = 0.0
        #: Приложение выключается — новых проходов не начинать.
        self._stopping = threading.Event()

    # -- состояние ----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "embed_enabled", False))

    @property
    def model(self) -> str:
        return str(getattr(self.settings, "embed_model", "") or "")

    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def status(self, *, fresh: bool = False) -> Dict[str, Any]:
        """Состояние смыслового поиска — то, что показывает библиотека.

        ``chunks`` — сколько фрагментов вообще, ``vectors`` — сколько из них
        с вектором нужной модели, ``missing`` — разница. ``stale`` считает
        векторы, построенные ЧУЖОЙ моделью: их не видно ни в ``vectors``, ни
        в ``missing`` по отдельности, а поиск от них слепнет целиком.
        """
        now = time.monotonic()
        with self._lock:
            fresh_enough = (
                self._status is not None
                and not fresh
                and now - self._status_at < STATUS_TTL
            )
            if fresh_enough:
                counted = dict(self._status or {})
            else:
                counted = None
        if counted is None:
            counted = self._count()
            with self._lock:
                self._status = dict(counted)
                self._status_at = now
        with self._lock:
            counted.update({
                "enabled": self.enabled,
                "model": self.model,
                "running": self._thread is not None and self._thread.is_alive(),
                "done": self._done,
                "total": self._total,
                "written": self._written,
                "error": self._error,
            })
        # «Чужие» векторы отдельной проверки не требуют: такой фрагмент
        # уже посчитан недостающим — вектора нужной модели у него нет.
        counted["ready"] = bool(
            counted["enabled"] and counted["chunks"] and not counted["missing"]
        )
        counted["hint"] = _hint(counted)
        return counted

    def _count(self) -> Dict[str, Any]:
        db = self.repos.db
        chunks = int(db.scalar("SELECT count(*) FROM chunks") or 0)
        # Считаем не строки таблицы векторов, а ФРАГМЕНТЫ: в таблице могут
        # остаться векторы удалённых документов, и «векторов больше, чем
        # фрагментов» пугало бы на пустом месте.
        vectors = int(db.scalar(
            "SELECT count(*) FROM chunks c "
            "JOIN embeddings e ON e.chunk_uid = c.chunk_uid "
            "WHERE e.model = ?", (self.model,)) or 0)
        # Фрагмент, вектор которого построен ЧУЖОЙ моделью. Косинус между
        # разными моделями ничего не значит, поэтому такой фрагмент для
        # поиска всё равно что без вектора — он входит и в missing, но
        # чинится иначе: не достройкой, а полной перестройкой.
        stale = int(db.scalar(
            "SELECT count(*) FROM chunks c "
            "JOIN embeddings e ON e.chunk_uid = c.chunk_uid "
            "WHERE e.model <> ?", (self.model,)) or 0)
        return {
            "chunks": chunks,
            "vectors": vectors,
            "missing": max(0, chunks - vectors),
            "stale": stale,
        }

    # -- построение ---------------------------------------------------------

    def start(self, *, force: bool = False) -> Dict[str, Any]:
        """Запустить построение в фоне. Уже идёт — вернуть текущее состояние."""
        if not self.enabled:
            return self.status()
        # Поток ЗАПУСКАЕМ под замком. Thread.is_alive() до start() ложно, и
        # если отпустить замок между «положили в self._thread» и «запустили»,
        # второй вызов увидит мёртвый поток и заведёт второй: два построения
        # поделят видеокарту, а wait() будет знать только про последнее.
        # Сам start() не ждёт тела потока, так что взаимоблокировки нет.
        #
        # А вот status() под замком звать нельзя: он берёт тот же замок, а
        # обычный threading.Lock не повторный.
        started = False
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._done = 0
                self._total = 0
                self._written = 0
                self._error = ""
                self._status = None
                self._thread = threading.Thread(
                    target=self._run, args=(force,),
                    name="reportgen-embed", daemon=True)
                self._thread.start()
                started = True
        return self.status(fresh=True) if started else self.status()

    def start_if_needed(self) -> Dict[str, Any]:
        """Достроить недостающие векторы — зовётся после приёма документов.

        Ничего не делает, если смысловой поиск выключен или всё построено:
        иначе каждая загрузка файла будила бы поток впустую.
        """
        if not self.enabled:
            return self.status()
        state = self.status(fresh=True)
        if state["running"] or not state["missing"]:
            return state
        return self.start()

    def wait(self, timeout: float | None = None) -> None:
        """Дождаться конца работы. Нужно тестам и остановке приложения."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def stop(self, timeout: float = 5.0) -> None:
        """Остановить построение при выключении приложения.

        Досчитать пачку не мешаем — она короткая, а брошенная на полуслове
        транзакция хуже. Просим не начинать следующую и ждём.
        """
        self._stopping.set()
        self.wait(timeout)

    def _default_client(self) -> Any:
        return EmbeddingClient(
            base_url=self.settings.embed_base_url,
            model=self.settings.embed_model,
            api_key=self.settings.embed_api_key,
            timeout=self.settings.embed_timeout,
            batch=self.settings.embed_batch,
        )

    def _run(self, force: bool) -> None:
        """Проходы построения. Их несколько: библиотека растёт на ходу.

        Пока идёт долгая книга, инженер успевает положить вторую. Её чанки
        в снимок первого прохода не попали, а ``start_if_needed`` в это
        время отвечает «уже строим» и ничего не ставит в очередь. Раньше
        второй документ так и оставался без векторов навсегда — ровно та
        беда, ради которой писан весь модуль. Поэтому в конце прохода
        смотрим заново: появилось недостающее — идём ещё раз.

        Проходов не больше ``_MAX_PASSES``: если после прохода недостающее
        не убавилось, дело не в новых документах, и крутиться незачем.
        """
        try:
            client = self.client_factory()
        except Exception as error:  # noqa: BLE001 — поток не должен уносить приложение
            self._fail(f"не удалось подключиться к службе эмбеддингов: {error}")
            return

        def progress(done: int, total: int) -> None:
            with self._lock:
                self._done = done
                self._total = total

        total_written = 0
        try:
            for step in range(_MAX_PASSES):
                if self._stopping.is_set():
                    break
                try:
                    written = index_embeddings(
                        self.repos, client, batch=self.settings.embed_batch,
                        # Полная перестройка нужна один раз: следующие проходы
                        # достраивают то, что появилось за время работы.
                        only_missing=not (force and step == 0),
                        progress=progress)
                except EmbeddingError as error:
                    # Служба не отвечает или отвечает не тем. Это штатная беда
                    # изолированной машины: сервер эмбеддингов не подняли.
                    # Поиск при этом работает словами — падать нельзя,
                    # молчать тоже.
                    self._fail(str(error), written=total_written)
                    return
                total_written += written
                with self._lock:
                    self._written = total_written
                    self._status = None
                if not written or not self._count()["missing"]:
                    break
        except Exception as error:  # noqa: BLE001 — поток не должен уносить приложение
            self._fail(f"построение векторов прервалось: {error}", written=total_written)
            return
        finally:
            # Соединение потока закрываем за собой: список соединений базы
            # не чистится сам, а поток на каждое построение новый — за
            # полгода работы отдела это сотни навсегда открытых файлов.
            self._release()
        with self._lock:
            self._written = total_written
            self._finished_at = time.monotonic()
            self._status = None

    def _fail(self, message: str, *, written: int = 0) -> None:
        with self._lock:
            self._error = message
            self._written = written
            self._finished_at = time.monotonic()
            self._status = None

    def _release(self) -> None:
        release = getattr(self.repos.db, "release", None)
        if release is not None:
            try:
                release()
            except Exception:          # noqa: BLE001 — закрытие не обязано мешать
                pass


def _hint(state: Dict[str, Any]) -> str:
    """Одна строка о состоянии поиска — та, что читает человек."""
    if not state["enabled"]:
        return ("смысловой поиск выключен: находится только то, что совпало "
                "словами — английские документы почти не находятся")
    if state["error"]:
        return f"векторы не строятся: {state['error']}"
    if state["running"]:
        total = state["total"] or 0
        done = state["done"] or 0
        # В первые доли секунды работа уже идёт, а сколько её — ещё неизвестно:
        # «0 из 0 фрагментов» выглядит поломкой, хотя всё в порядке.
        if not total:
            return "строятся векторы"
        return f"строятся векторы: {done} из {total} фрагментов"
    if not state["chunks"]:
        return "библиотека пуста"
    if state["missing"] and state["stale"]:
        return (f"векторов нет у {state['missing']} фрагментов, ещё "
                f"{state['stale']} построены другой моделью — смысловой поиск "
                f"работает не по всей библиотеке")
    if state["missing"]:
        return (f"векторов нет у {state['missing']} фрагментов из "
                f"{state['chunks']} — по ним ищется только словами")
    if state["stale"]:
        return (f"{state['stale']} векторов построены другой моделью, не "
                f"«{state['model']}» — постройте заново")
    return "смысловой поиск работает по всей библиотеке"
