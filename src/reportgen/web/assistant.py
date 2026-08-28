"""Помощник: вопросы и ответы по технической библиотеке компании.

Второй режим работы системы помимо отчётов. Отличие в дисциплине: в отчёте
числа берутся только из факт-пакета, а в разговоре — только из найденных
фрагментов библиотеки, и каждое такое утверждение сопровождается ссылкой.
Общее знание модели допускается, но обязано быть помечено как непроверенное:
инженер должен видеть, где кончается ваша библиотека и начинается догадка.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Sequence

from ..prompts import ASSISTANT_PROMPT, ASSISTANT_SYSTEM_PROMPT, ASSISTANT_TITLE_PROMPT
from ..retrieval import Hit
from ..store.models import ATTACHMENT_TITLES, Chat, ChatMessage, User
from .service import ReportService, ServiceError

HISTORY_DEPTH = 6
HISTORY_CHARS = 1500
#: Сколько заголовков разделов показывать в оглавлении одного документа.
OUTLINE_HEADINGS = 30
#: Сколько знаков библиотеки остаётся при любых вложениях: без источников
#: помощник превращается в обычную модель без ссылок на нормы.
MIN_LIBRARY_CHARS = 6000
#: Запасное значение, если в настройках его нет (старый settings.json).
SOURCE_CHARS = 1400
MAX_QUESTION = 4000
DEFAULT_TITLE = "Новый разговор"


@dataclass
class AssistantService:
    """Операции над разговорами. Поиск и модель переиспользуются из отчётного слоя."""

    reports: ReportService

    @property
    def repos(self):
        return self.reports.repos

    @property
    def settings(self):
        return self.reports.settings

    # -- разговоры ----------------------------------------------------------

    def list_chats(self, user: User, *, archived: bool = False) -> List[Chat]:
        return self.repos.chats.for_user(user.id, archived=archived)

    def create_chat(self, user: User, *, title: str = DEFAULT_TITLE,
                    domain: str = "", case_ref: int | None = None) -> Chat:
        if case_ref is not None and self.repos.cases.get(case_ref) is None:
            raise ServiceError("письмо, к которому привязывается разговор, не найдено", 404)
        return self.repos.chats.create(user.id, title=title, domain=domain, case_ref=case_ref)

    def get_chat(self, user: User, chat_id: int) -> Chat:
        chat = self.repos.chats.get(chat_id, user.id)
        if chat is None:
            # Чужой чат и несуществующий чат неразличимы снаружи намеренно.
            raise ServiceError("разговор не найден", 404)
        return chat

    def messages(self, user: User, chat_id: int) -> List[ChatMessage]:
        self.get_chat(user, chat_id)
        return self.repos.chats.messages(chat_id)

    def rename(self, user: User, chat_id: int, title: str) -> Chat:
        self.get_chat(user, chat_id)
        self.repos.chats.rename(chat_id, title)
        return self.get_chat(user, chat_id)

    def update(self, user: User, chat_id: int, *, domain: str | None = None,
               archived: bool | None = None) -> Chat:
        self.get_chat(user, chat_id)
        self.repos.chats.update(chat_id, domain=domain, archived=archived)
        return self.get_chat(user, chat_id)

    def delete(self, user: User, chat_id: int) -> None:
        self.get_chat(user, chat_id)
        self.repos.chats.delete(chat_id)
        self.repos.audit.log("chat.delete", user=user, object_type="chat", object_id=str(chat_id))

    # -- ответ --------------------------------------------------------------

    def ask(self, user: User, chat_id: int, question: str, *,
            top_k: int | None = None) -> Dict[str, Any]:
        """Полный ответ одним куском (без потоковой выдачи)."""
        prepared = self._prepare(user, chat_id, question, top_k=top_k)
        text = self.reports.get_llm().complete(
            ASSISTANT_SYSTEM_PROMPT, prepared["prompt"],
            max_tokens=self._max_tokens(), temperature=0.3,
            history=prepared["history"],
        )
        return self._finish(user, prepared, text)

    def ask_stream(self, user: User, chat_id: int, question: str, *,
                   top_k: int | None = None) -> Iterator[Dict[str, Any]]:
        """Потоковый ответ: источники сразу, текст по мере генерации.

        Инженер видит, на чём основан ответ, ещё до того как модель дописала
        первое предложение — это заметно меняет ощущение от работы.
        """
        prepared = self._prepare(user, chat_id, question, top_k=top_k)
        yield {"type": "question", "message": prepared["question_message"].to_dict()}
        yield {
            "type": "sources",
            "sources": prepared["sources"],
            "documents": prepared.get("documents") or [],
            "expansion": prepared.get("expansion") or None,
            "warning": prepared.get("warning") or None,
        }

        llm = self.reports.get_llm()
        pieces: List[str] = []
        stream = getattr(llm, "stream", None)
        try:
            if stream is None:
                text = llm.complete(ASSISTANT_SYSTEM_PROMPT, prepared["prompt"],
                                    max_tokens=self._max_tokens(), temperature=0.3,
                                    history=prepared["history"])
                pieces.append(text)
                yield {"type": "delta", "text": text}
            else:
                for piece in stream(ASSISTANT_SYSTEM_PROMPT, prepared["prompt"],
                                    max_tokens=self._max_tokens(), temperature=0.3,
                                    history=prepared["history"]):
                    pieces.append(piece)
                    yield {"type": "delta", "text": piece}
        except GeneratorExit:
            # Браузер отсоединился: инженер закрыл вкладку или ушёл в другой
            # раздел. Сгенерированное к этому моменту всё равно сохраняем —
            # иначе минута работы модели пропадает бесследно, а в разговоре
            # остаётся вопрос без ответа. Помечаем ответ как прерванный.
            if pieces:
                self._finish(user, prepared, "".join(pieces), interrupted=True)
            raise

        result = self._finish(user, prepared, "".join(pieces))
        yield {"type": "done", **result}

    # -- внутреннее ---------------------------------------------------------

    def _prepare(self, user: User, chat_id: int, question: str,
                 *, top_k: int | None) -> Dict[str, Any]:
        chat = self.get_chat(user, chat_id)
        question = (question or "").strip()
        if not question:
            raise ServiceError("пустой вопрос", 400)
        if len(question) > MAX_QUESTION:
            raise ServiceError(f"вопрос длиннее {MAX_QUESTION} символов", 400)

        history = [
            {"role": message.role, "content": _clip(message.content, HISTORY_CHARS)}
            for message in self.repos.chats.tail(chat.id, HISTORY_DEPTH)
        ]
        question_message = self.repos.chats.add_message(chat.id, "user", question)

        # Вложения привязываем к отправленному вопросу: в разговоре видно,
        # с какими файлами он был задан.
        attachments = self.repos.chats.attachments(chat.id, pending_only=True)
        if attachments:
            self.repos.chats.bind_attachments(chat.id, question_message.id)

        hits = self._search(chat, question, history, top_k, attachments=attachments)
        retriever = self.reports.get_retriever()
        # Половина библиотеки английская, а спрашивают по-русски. Если запрос
        # дополнен по двуязычному словарю — сказать об этом: иначе английский
        # фрагмент в источниках выглядит взявшимся ниоткуда. Заодно доносим
        # предупреждение поиска: падение службы эмбеддингов в чате раньше не
        # было видно вовсе, а поиск при этом работал вполсилы.
        expansion = list(getattr(retriever, "last_expansion", []) or [])
        warning = getattr(retriever, "last_warning", "") or ""

        attachment_block, attachment_chars = self._attachment_block(attachments)
        sources = self._build_sources(hits, reserved=attachment_chars)
        documents = self._document_cards(sources)
        prompt = ASSISTANT_PROMPT.format(
            question=question,
            case_block=self._case_block(chat),
            attachments=attachment_block,
            library_map=_render_map(documents),
            sources=_render_sources(sources, documents),
            target_words=int(getattr(self.settings, "assistant_target_words", 0) or 500),
        )
        return {
            "chat": chat,
            "question": question,
            "question_message": question_message,
            "history": history,
            "sources": sources,
            "documents": documents,
            "attachments": [item.to_dict() for item in attachments],
            "expansion": expansion,
            "warning": warning,
            "prompt": prompt,
        }

    # -- сборка материала ---------------------------------------------------

    def _attachment_block(self, attachments: Sequence[Any]) -> tuple[str, int]:
        """Приложенные файлы для промпта и их вес в знаках.

        Дамп на десятки мегабайт в окно модели не поместится никогда, поэтому
        берём начало: там заголовки сессии и первые ошибки, по которым обычно
        и понятно, что случилось. Об обрезке говорим прямо — иначе модель
        сделает вывод «ошибок больше нет» по обрезанному хвосту.
        """
        if not attachments:
            return "", 0
        limit = int(getattr(self.settings, "assistant_attachment_chars", 0) or 8000)
        blocks = []
        for item in attachments:
            text = (item.text or "").strip()
            if not text:
                blocks.append(
                    f"[Файл: {item.name}] текст извлечь не удалось"
                    + (f" — {item.note}" if item.note else "")
                )
                continue
            cut = len(text) > limit
            body = text[:limit].rstrip() + ("\n…(файл показан не целиком)" if cut else "")
            head = f"[Файл: {item.name}, {ATTACHMENT_TITLES.get(item.kind, item.kind)}"
            if cut:
                head += f", показано {limit} из {len(text)} знаков"
            head += "]"
            blocks.append(f"{head}\n{body}")
        block = "\n### ПРИЛОЖЕННЫЕ ФАЙЛЫ\n" + "\n\n".join(blocks) + "\n"
        return block, len(block)

    def _build_sources(self, hits: Sequence[Hit], *, reserved: int = 0) -> List[Dict[str, Any]]:
        """Фрагменты для промпта: с соседями и в пределах окна контекста.

        Три вещи, которых раньше не было.

        Соседи. Найденный фрагмент — это окно в 1800 знаков, вырезанное из
        документа механически. Таблица параметров или описание поля кадра
        в него не помещается: начало осталось в предыдущем куске, конец — в
        следующем. Модель видела середину и отвечала по середине.

        Порядок. Фрагменты идут группами по документам, а не вперемешку по
        весу: так видно, что стандарт говорит одно, а паспорт микросхемы
        другое, и их можно сопоставить.

        Бюджет. Материал обрезается по assistant_context_chars — иначе
        llama.cpp молча выбрасывает начало промпта вместе с системной
        инструкцией, и модель перестаёт ставить ссылки. Обрезаем с конца
        выдачи: там уже хвост относимости.
        """
        if not hits:
            return []

        # Приложенные файлы уже заняли часть окна — остаток идёт библиотеке.
        # Совсем без источников не оставляем: отвечать будет не на что даже
        # по нормам, а именно за этим помощника и спрашивают. Но и выше
        # настройки не поднимаемся: если окно модели маленькое и это задано
        # осознанно, порог его не отменяет.
        budget = int(getattr(self.settings, "assistant_context_chars", 0) or 26000)
        if reserved > 0:
            budget = max(budget - reserved, min(MIN_LIBRARY_CHARS, budget))
        limit = int(getattr(self.settings, "assistant_source_chars", 0) or SOURCE_CHARS)
        radius = int(getattr(self.settings, "assistant_neighbours", 0) or 0)
        neighbour_top = int(getattr(self.settings, "assistant_neighbour_top", 0) or 0)

        around: Dict[str, List[Any]] = {}
        if radius > 0 and neighbour_top > 0:
            anchors = [hit.chunk.chunk_id for hit in hits[:neighbour_top]]
            try:
                around = self.repos.chunks.neighbours(anchors, radius=radius)
            except Exception:      # noqa: BLE001 — соседи не обязательны
                around = {}

        # Сосед, который и сам попал в выдачу, второй раз не нужен: тот же
        # текст занимал бы окно дважды. На демонстрационной библиотеке это
        # съедало примерно четверть материала.
        found = {hit.chunk.chunk_id for hit in hits}

        sources: List[Dict[str, Any]] = []
        spent = 0
        for hit in hits:
            chunk = hit.chunk
            text = _tidy(chunk.text, limit)
            before, after = _split_neighbours(
                around.get(chunk.chunk_id, []), chunk.chunk_id, skip=found)
            # Соседям хватает половины меры: они нужны как продолжение, а не
            # как самостоятельный источник.
            half = max(limit // 2, 300)
            lead = _tidy(before, half) if before else ""
            tail = _tidy(after, half) if after else ""

            cost = len(text) + len(lead) + len(tail) + len(chunk.citation) + 40
            if sources and spent + cost > budget:
                break                    # хвост выдачи в окно уже не влезает
            spent += cost

            sources.append({
                "label": f"S{len(sources) + 1}",
                "chunk_uid": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "citation": chunk.citation,
                "doc_type": chunk.doc_type,
                "domain": chunk.meta.get("domain", ""),
                "status": chunk.meta.get("status", "current"),
                "year": chunk.meta.get("year"),
                "title": chunk.meta.get("title", chunk.doc_id),
                "breadcrumbs": chunk.breadcrumbs,
                "text": text,
                "lead": lead,
                "tail": tail,
            })
        return sources

    def _document_cards(self, sources: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Карточки документов: чем каждый полезен и что в нём ещё есть."""
        if not sources:
            return []
        order: List[str] = []
        cards: Dict[str, Dict[str, Any]] = {}
        for item in sources:
            doc_id = item["doc_id"]
            if doc_id not in cards:
                order.append(doc_id)
                cards[doc_id] = {
                    "doc_id": doc_id,
                    "title": item["title"],
                    "doc_type": item["doc_type"],
                    "year": item["year"],
                    "status": item["status"],
                    "labels": [],
                    "outline": [],
                }
            cards[doc_id]["labels"].append(item["label"])

        if getattr(self.settings, "assistant_outlines", True):
            try:
                outlines = self.repos.chunks.outline(order, limit=OUTLINE_HEADINGS)
            except Exception:          # noqa: BLE001 — оглавление не обязательно
                outlines = {}
            for doc_id, headings in outlines.items():
                if doc_id in cards:
                    cards[doc_id]["outline"] = headings
        return [cards[doc_id] for doc_id in order]

    def _finish(self, user: User, prepared: Dict[str, Any], text: str,
                *, interrupted: bool = False) -> Dict[str, Any]:
        chat: Chat = prepared["chat"]
        sources: List[Dict[str, Any]] = prepared["sources"]
        used = _used_labels(text)
        # В историю кладём только те источники, на которые ответ реально сослался,
        # иначе панель источников заполняется мусором.
        kept = [item for item in sources if item["label"] in used] or sources[:3]
        # Соседние куски нужны были модели, в панель источников они не идут:
        # инженер открывает документ целиком одним нажатием.
        kept = [{k: v for k, v in item.items() if k not in ("lead", "tail")} for item in kept]
        answer = self.repos.chats.add_message(
            chat.id, "assistant", text.strip(),
            sources=kept,
            meta={"model": getattr(self.reports.get_llm(), "name", "unknown"),
                  "found": len(sources), "cited": len(used),
                  "documents": len(prepared.get("documents") or []),
                  "interrupted": interrupted},
        )
        if chat.title == DEFAULT_TITLE:
            self.repos.chats.rename(chat.id, _make_title(prepared["question"]))
        self.repos.audit.log(
            "chat.ask", user=user, object_type="chat", object_id=str(chat.id),
            details={"found": len(sources), "cited": len(used)},
        )
        return {
            "question": prepared["question_message"].to_dict(),
            "answer": answer.to_dict(),
            "chat": self.get_chat(user, chat.id).to_dict(),
            "documents": prepared.get("documents") or [],
            "expansion": prepared.get("expansion") or None,
            "warning": prepared.get("warning") or None,
        }

    def _max_tokens(self) -> int:
        """Потолок длины ответа. Настройка, а не число в коде."""
        return int(getattr(self.settings, "assistant_max_tokens", 0) or 4000)

    def _source_chars(self) -> int:
        """Сколько знаков фрагмента видит модель.

        Главный рычаг развёрнутости: короткий фрагмент обрывается на середине
        таблицы допусков, и писать модели просто не из чего.
        """
        return int(getattr(self.settings, "assistant_source_chars", 0) or SOURCE_CHARS)

    def _search(self, chat: Chat, question: str, history: Sequence[Dict[str, str]],
                top_k: int | None, attachments: Sequence[Any] = ()) -> List[Hit]:
        retriever = self.reports.get_retriever()
        if retriever is None:
            return []
        # К запросу добавляем предыдущий вопрос инженера: «а для 16-QAM?» без
        # контекста не найдёт ничего.
        previous = [item["content"] for item in history if item["role"] == "user"][-1:]
        # Слова из приложенного файла тоже идут в запрос: по вопросу «что тут
        # не так?» без них не найдётся ничего, а в дампе есть имена полей и
        # коды ошибок, по которым библиотека находится сразу.
        keywords = _attachment_keywords(attachments)
        query = " ".join([question, *previous, keywords])[:1400]
        domains = [chat.domain] if chat.domain else None
        # Для разговора берём больше фрагментов, чем для отчёта: там материал
        # ограничен факт-пакетом, здесь — только вопросом. Лишнее всё равно
        # отсечёт бюджет окна в _build_sources.
        wanted = top_k or int(
            getattr(self.settings, "assistant_top_k", 0) or self.settings.retrieval_top_k
        )
        try:
            return retriever.search(query, top_k=wanted, domains=domains)
        except TypeError:
            # Поисковик без поддержки направлений (лексический запасной вариант).
            return retriever.search(query, top_k=wanted)

    def _case_block(self, chat: Chat) -> str:
        if not chat.case_ref:
            return ""
        case = self.repos.cases.get(chat.case_ref)
        if case is None:
            return ""
        try:
            facts = self.reports.facts_of(case)
        except ServiceError:
            return ""
        return (
            "\n### КОНТЕКСТ ОБРАЩЕНИЯ\n"
            f"{facts.render_header()}\n\n"
            f"{facts.render_measurements()}\n"
        )


#: Как называется тип документа в карточке для модели.
_DOC_TYPE_TITLES = {
    "literature": "литература",
    "standards": "стандарт",
    "datasheets": "паспорт микросхемы",
    "reports": "прошлый отчёт",
    "regulations": "регламент",
    "misc": "прочее",
}
_STATUS_TITLES = {
    "current": "действующий",
    "superseded": "ЗАМЕНЁН более новой редакцией",
    "archived": "выведен из обращения",
    "draft": "проект, не введён в действие",
}


def _render_map(documents: Sequence[Dict[str, Any]]) -> str:
    """Карта найденного: какие документы попали в выдачу и что в них есть.

    Без неё модель видит десяток разрозненных кусков и не знает ни того, из
    скольких документов они взяты, ни того, что в этих документах есть ещё.
    """
    if not documents:
        return "(ничего не нашлось)"
    blocks = []
    for card in documents:
        head = f"{card['title']} — {_DOC_TYPE_TITLES.get(card['doc_type'], card['doc_type'])}"
        if card.get("year"):
            head += f", {card['year']} г."
        head += f", {_STATUS_TITLES.get(card.get('status', 'current'), card.get('status'))}"
        head += f". Фрагменты: {', '.join(card['labels'])}"
        lines = [head]
        if card.get("outline"):
            lines.append("  Разделы документа: " + "; ".join(card["outline"]))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_sources(sources: Sequence[Dict[str, Any]],
                    documents: Sequence[Dict[str, Any]] | None = None) -> str:
    """Фрагменты для промпта, сгруппированные по документам.

    Порядок по документам, а не по весу выдачи: сопоставить стандарт с
    паспортом микросхемы можно, только когда они не перемешаны.
    """
    if not sources:
        return "(в библиотеке ничего подходящего не нашлось)"
    order = [card["doc_id"] for card in (documents or [])] or []
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in sources:
        grouped.setdefault(item.get("doc_id", ""), []).append(item)
    for doc_id in grouped:
        if doc_id not in order:
            order.append(doc_id)

    blocks = []
    for doc_id in order:
        items = grouped.get(doc_id) or []
        if not items:
            continue
        title = items[0].get("title") or doc_id
        blocks.append(f"— — — ДОКУМЕНТ: {title} — — —")
        for item in items:
            mark = "" if item.get("status", "current") == "current" else \
                f" [ВНИМАНИЕ: документ не действующий — {item['status']}]"
            body = item["text"]
            # Соседние куски помечаем: модель не должна цитировать «…» как
            # часть найденного фрагмента.
            if item.get("lead"):
                body = f"(предыдущий фрагмент документа)\n{item['lead']}\n\n{body}"
            if item.get("tail"):
                body = f"{body}\n\n(следующий фрагмент документа)\n{item['tail']}"
            blocks.append(f"[{item['label']}] {item['citation']}{mark}\n{body}")
    return "\n\n".join(blocks)


#: Сколько разных слов берём из приложенных файлов в поисковый запрос.
ATTACHMENT_KEYWORDS = 40


def _attachment_keywords(attachments: Sequence[Any]) -> str:
    """Разные слова из начала приложенных файлов.

    Именно разные. Сырое начало дампа брать нельзя: в логе одна и та же
    строка повторяется сотнями, запрос состоит из неё целиком, и поиск
    находит не то, о чём спрашивали, а то, что чаще всего повторяется
    в файле. Сам вопрос при этом тонет.
    """
    seen: List[str] = []
    known = set()
    for item in attachments:
        for word in re.split(r"[^0-9A-Za-zА-Яа-яЁё_.-]+", (item.text or "")[:4000]):
            if len(word) < 3:
                continue
            key = word.lower()
            if key in known:
                continue
            known.add(key)
            seen.append(word)
            if len(seen) >= ATTACHMENT_KEYWORDS:
                return " ".join(seen)
    return " ".join(seen)


def _split_neighbours(chunks: Sequence[Any], anchor_uid: str,
                      skip: set[str] | None = None) -> tuple[str, str]:
    """Разложить соседей на текст «до» и текст «после».

    Идентификатор фрагмента — «doc_id#0007» с ведущими нулями, поэтому
    сравнение строк совпадает со сравнением номеров: отдельного поля ord
    у Chunk нет, а тянуть его сюда ради одного сравнения незачем.

    ``skip`` — фрагменты, которые и сами попали в выдачу: их текст уже есть
    в промпте отдельным источником, повторять его нельзя.

    При radius > 1 соседей с каждой стороны несколько — склеиваем их по
    порядку, иначе дальние возвращались бы молча выброшенными.
    """
    skip = skip or set()
    before: List[str] = []
    after: List[str] = []
    for chunk in sorted(chunks, key=lambda item: item.chunk_id):
        if chunk.chunk_id in skip:
            continue
        (before if chunk.chunk_id < anchor_uid else after).append(chunk.text)
    return "\n\n".join(before), "\n\n".join(after)


def _used_labels(text: str) -> set[str]:
    return set(re.findall(r"\[(S\d+)\]", text or ""))


def _tidy(text: str, limit: int) -> str:
    """Фрагмент для промпта и панели источников. См. corpus.tidy_quote."""
    from ..corpus import tidy_quote  # noqa: PLC0415 — не тянуть корпус при импорте

    return tidy_quote(text, limit)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _make_title(question: str) -> str:
    words = " ".join(question.split())
    title = words[:60].rstrip()
    if len(words) > 60:
        title = title.rsplit(" ", 1)[0] + "…"
    return title or DEFAULT_TITLE
