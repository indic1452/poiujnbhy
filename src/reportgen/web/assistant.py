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
from ..store.models import Chat, ChatMessage, User
from .service import ReportService, ServiceError

HISTORY_DEPTH = 6
HISTORY_CHARS = 1500
SOURCE_CHARS = 900
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
            raise ServiceError("кейс, к которому привязывается разговор, не найден", 404)
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
            max_tokens=1600, temperature=0.3, history=prepared["history"],
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
        yield {"type": "sources", "sources": prepared["sources"]}

        llm = self.reports.get_llm()
        pieces: List[str] = []
        stream = getattr(llm, "stream", None)
        if stream is None:
            text = llm.complete(ASSISTANT_SYSTEM_PROMPT, prepared["prompt"],
                                max_tokens=1600, temperature=0.3)
            pieces.append(text)
            yield {"type": "delta", "text": text}
        else:
            for piece in stream(ASSISTANT_SYSTEM_PROMPT, prepared["prompt"],
                                max_tokens=1600, temperature=0.3,
                                history=prepared["history"]):
                pieces.append(piece)
                yield {"type": "delta", "text": piece}

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

        hits = self._search(chat, question, history, top_k)
        sources = [
            {
                "label": f"S{index}",
                "chunk_uid": hit.chunk.chunk_id,
                "citation": hit.chunk.citation,
                "doc_type": hit.chunk.doc_type,
                "domain": hit.chunk.meta.get("domain", ""),
                "text": _clip(" ".join(hit.chunk.text.split()), SOURCE_CHARS),
            }
            for index, hit in enumerate(hits, start=1)
        ]
        prompt = ASSISTANT_PROMPT.format(
            question=question,
            case_block=self._case_block(chat),
            sources=_render_sources(sources),
        )
        return {
            "chat": chat,
            "question": question,
            "question_message": question_message,
            "history": history,
            "sources": sources,
            "prompt": prompt,
        }

    def _finish(self, user: User, prepared: Dict[str, Any], text: str) -> Dict[str, Any]:
        chat: Chat = prepared["chat"]
        sources: List[Dict[str, Any]] = prepared["sources"]
        used = _used_labels(text)
        # В историю кладём только те источники, на которые ответ реально сослался,
        # иначе панель источников заполняется мусором.
        kept = [item for item in sources if item["label"] in used] or sources[:3]
        answer = self.repos.chats.add_message(
            chat.id, "assistant", text.strip(),
            sources=kept,
            meta={"model": getattr(self.reports.get_llm(), "name", "unknown"),
                  "found": len(sources), "cited": len(used)},
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
        }

    def _search(self, chat: Chat, question: str, history: Sequence[Dict[str, str]],
                top_k: int | None) -> List[Hit]:
        retriever = self.reports.get_retriever()
        if retriever is None:
            return []
        # К запросу добавляем предыдущий вопрос инженера: «а для 16-QAM?» без
        # контекста не найдёт ничего.
        previous = [item["content"] for item in history if item["role"] == "user"][-1:]
        query = " ".join([question, *previous])[:1000]
        domains = [chat.domain] if chat.domain else None
        try:
            return retriever.search(
                query, top_k=top_k or self.settings.retrieval_top_k, domains=domains,
            )
        except TypeError:
            # Поисковик без поддержки направлений (лексический запасной вариант).
            return retriever.search(query, top_k=top_k or self.settings.retrieval_top_k)

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


def _render_sources(sources: Sequence[Dict[str, Any]]) -> str:
    if not sources:
        return "(в библиотеке ничего подходящего не нашлось)"
    return "\n\n".join(
        f"[{item['label']}] {item['citation']}\n{item['text']}" for item in sources
    )


def _used_labels(text: str) -> set[str]:
    return set(re.findall(r"\[(S\d+)\]", text or ""))


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _make_title(question: str) -> str:
    words = " ".join(question.split())
    title = words[:60].rstrip()
    if len(words) > 60:
        title = title.rsplit(" ", 1)[0] + "…"
    return title or DEFAULT_TITLE
