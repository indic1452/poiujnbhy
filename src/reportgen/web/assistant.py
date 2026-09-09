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
from typing import Any, Callable, Dict, Iterator, List, Sequence

from ..prompts import (
    ASSISTANT_PROMPT,
    ASSISTANT_SYSTEM_PROMPT,
    ASSISTANT_TITLE_PROMPT,
    RESEARCH_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
)
from ..retrieval import BM25Index, Hit, reciprocal_rank_fusion
from ..store.models import ATTACHMENT_TITLES, Chat, ChatMessage, User
from .catalog import (
    CATALOG_CHARS,
    CATALOG_SHELVES_CHARS,
    LibraryCatalog,
    render_catalog,
)
from .research import (
    Step,
    found_note,
    parse_step,
    render_found,
    render_trail,
)
from .service import ReportService, ServiceError


#: Слова и числа порознь: «RFC4818» — это ['rfc', '4818'], как и «RFC 4818».
_NAME_PARTS = re.compile(r"[0-9]+|[a-zа-яё]+")

#: Расширения, которые инженер называет вместе с документом («rfc4818.txt»).
#: Отбрасываем только в конце и только когда перед ними что-то есть.
_FILE_SUFFIXES = frozenset(
    ("txt", "pdf", "md", "html", "htm", "xml", "doc", "docx",
     "rtf", "odt", "djvu", "ps")
)


def name_parts(text: str) -> List[str]:
    """Имя документа как последовательность слов и чисел.

    Написание разное — «RFC 4818», «RFC4818», «RFC-4818», «rfc4818.txt», —
    а последовательность одна: ['rfc', '4818']. По ней и сличаем. Сличать
    подстроками нельзя: «RFC 481» является подстрокой «RFC 4818», и запрос об
    одном документе открывал другой.
    """
    части = _NAME_PARTS.findall(str(text).casefold().replace("ё", "е"))
    if len(части) > 1 and части[-1] in _FILE_SUFFIXES:
        части = части[:-1]
    return части


#: Обозначения документов, которые в отделе называют по номеру. Список
#: намеренно закрытый: выуживать документ из каждого числа нельзя, иначе
#: «полоса 30 кГц» и «усреднение по 64 сегментам» превратятся в стандарты.
_DESIGNATION_PREFIXES = (
    "RFC", "STD", "BCP", "FYI",
    "ГОСТ Р", "ГОСТ", "ОСТ", "РД", "СНиП", "ТУ",
    "IEEE", "ISO/IEC", "ISO", "IEC", "ETSI", "EN", "ANSI", "TIA", "3GPP",
)

#: «RFC 4818», «RFC4818», «RFC-4818», «ГОСТ Р 53363-2009».
_DESIGNATION_RE = re.compile(
    r"\b(?P<prefix>" + "|".join(
        часть.replace(" ", r"\s+").replace("/", r"\s*/\s*")
        for часть in _DESIGNATION_PREFIXES
    ) + r")\s*[-—–]?\s*(?P<number>\d{1,5}(?:\.\d+[a-zA-Zа-яА-Я]*)?(?:-\d{1,4})?)\b",
    re.IGNORECASE,
)

#: Рекомендации МСЭ называют буквой с номером: G.703, X.25, Y.1541. Само по
#: себе «G.703» слишком похоже на обычное число с точкой, поэтому берём его
#: только рядом со словами, которые в отделе стоят перед рекомендацией.
_ITU_RE = re.compile(
    r"(?:МСЭ|ITU|Рекомендаци\w*|Recommendation)\s*[-—–]?\s*"
    r"(?:[TRSТР]\b[.\s-]*)?"
    r"(?P<code>[A-ZА-Я]\.\d{1,4}(?:\.\d+)*)",
    re.IGNORECASE,
)

#: Сколько обозначений разбираем. Вопрос со списком из полусотни номеров не
#: должен превращаться в полсотни запросов к описи на каждом заходе.
MAX_DESIGNATIONS = 8


def designations_in(text: str) -> List[str]:
    """Обозначения документов, названные в тексте, в порядке появления.

    Нужны, чтобы система могла свериться с описью САМА, а не надеяться, что
    поиск случайно вытянет нужный документ, а модель случайно не соврёт про
    его отсутствие. «Есть ли у нас RFC 4818» — вопрос, на который в базе есть
    точный ответ; узнавать его перебором фрагментов незачем.
    """
    найдено: List[str] = []
    видели = set()

    def добавить(значение: str) -> None:
        ключ = "".join(name_parts(значение))
        if ключ and ключ not in видели:
            видели.add(ключ)
            найдено.append(значение)

    строка = str(text or "")
    for match in _DESIGNATION_RE.finditer(строка):
        приставка = re.sub(r"\s+", " ", match.group("prefix")).strip()
        # Приставку приводим к тому виду, в котором её пишут в названиях:
        # латиницу заглавными, русское «ГОСТ Р» — как в списке.
        для_списка = next(
            (образец for образец in _DESIGNATION_PREFIXES
             if "".join(name_parts(образец)) == "".join(name_parts(приставка))),
            приставка,
        )
        добавить(f"{для_списка} {match.group('number')}")
    for match in _ITU_RE.finditer(строка):
        добавить(match.group("code").upper())
    return найдено[:MAX_DESIGNATIONS]


def _подряд(где: Sequence[str], что: Sequence[str]) -> bool:
    """Идёт ли ``что`` внутри ``где`` подряд, слово в слово."""
    if not что or len(что) > len(где):
        return False
    return any(list(где[i:i + len(что)]) == list(что)
               for i in range(len(где) - len(что) + 1))

HISTORY_DEPTH = 6
HISTORY_CHARS = 1500
#: Сколько заголовков разделов показывать в оглавлении одного документа.
OUTLINE_HEADINGS = 30
#: Сколько знаков библиотеки остаётся при любых вложениях: без источников
#: помощник превращается в обычную модель без ссылок на нормы.
MIN_LIBRARY_CHARS = 6000
#: Сколько знаков приложенного файла остаётся при любом окне. Меньше — и
#: показывать нечего: в первой тысяче знаков дампа стоят заголовки сессии,
#: по которым обычно и видно, что случилось.
MIN_ATTACHMENT_CHARS = 1000
#: Запасное значение, если в настройках его нет (старый settings.json).
SOURCE_CHARS = 1400
#: Потолок для куска соседнего фрагмента. Сосед нужен как продолжение мысли,
#: оборванной на границе нарезки, — и на это довольно шестисот знаков. Выше
#: он начинает вытеснять из окна другие найденные документы, а один документ,
#: показанный с трёх сторон, отвечает хуже трёх разных.
NEIGHBOUR_CHARS = 600
MAX_QUESTION = 4000
#: Сколько фрагментов названного документа подкладываем в источники, когда
#: поиск до него не дотянулся. Не весь документ: задача — не подменить поиск,
#: а не дать помощнику отрицать существование того, что лежит на полке.
PINNED_CHUNKS = 3
#: Докуда смотрим внутри такого документа, выбирая подходящие фрагменты. В
#: библиотеке отдела попадаются книги в полторы тысячи фрагментов, и перебирать
#: их целиком на каждый вопрос незачем.
PIN_SCAN_CHUNKS = 400
#: Сколько документов рассматриваем при опознании по номеру. Номер отсекает
#: библиотеку до единиц строк, и двухсот с запасом хватает на все редакции.
CANDIDATE_LIMIT = 200
#: Сколько заходов разбора делаем, если в настройках ничего не сказано.
#: Как отвечает помощник. Переключатель стоит в самом разговоре: один и тот же
#: инженер утром разбирает дамп по протоколу, а днём спрашивает «что такое FCS»,
#: и одна настройка на всех тут не годится.
#:
#: «Глубоко» — то, что стоит в настройках отдела: разбор в несколько заходов
#: (нашёл, прочитал, понял чего не хватает, поискал снова) и длинный ответ.
#: Точнее и с бо́льшим числом источников, но это минуты.
#:
#: «Быстро» — один поиск и короткий ответ. Правила ответа те же самые: ссылки
#: на источники, честное «в библиотеке этого нет», оригинальные названия полей.
#: Экономим на объёме работы, а не на добросовестности.
CHAT_MODES = ("deep", "fast")
DEFAULT_CHAT_MODE = "deep"

#: Потолки быстрого режима. Не замена настройкам, а именно потолок: берётся
#: минимум из настройки и этого числа. Поэтому отдел, опустивший
#: assistant_rounds до нуля, не получит «быстрый» режим медленнее глубокого.
FAST_LIMITS = {
    "rounds": 0,            # без заходов: один поиск, как было до разбора
    "top_k": 8,             # меньше фрагментов — короче промпт, быстрее разбор
    "target_words": 180,    # ответ на полстраницы, а не на две
    "max_tokens": 1200,     # главный рычаг: текст рождается по слову
}


def chat_mode(chat: "Chat | None") -> str:
    """Режим разговора, с запасом на старые записи и мусор в поле."""
    режим = str(getattr(chat, "mode", "") or "").strip().lower()
    return режим if режим in CHAT_MODES else DEFAULT_CHAT_MODE


RESEARCH_ROUNDS = 4
#: Длина ответа планировщика: одна строка, лишнее только мешает.
RESEARCH_TOKENS = 120
#: Сколько фрагментов отдаёт один заход поиска внутри разбора.
RESEARCH_TOP_K = 6
#: Сколько кусков документа отдаёт «ЧИТАТЬ».
RESEARCH_READ_CHUNKS = 4
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
               archived: bool | None = None, mode: str | None = None) -> Chat:
        self.get_chat(user, chat_id)
        if mode is not None and mode not in CHAT_MODES:
            raise ServiceError(
                "неизвестный режим ответа '%s'; допустимы: %s"
                % (mode, ", ".join(CHAT_MODES)), 400)
        self.repos.chats.update(chat_id, domain=domain, archived=archived, mode=mode)
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
            max_tokens=prepared["profile"]["max_tokens"], temperature=0.3,
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
        # Ход разбора инженер должен видеть: ответ «в библиотеке этого нет»
        # без списка того, что искали, невозможно ни проверить, ни оспорить.
        for title in prepared.get("trail") or []:
            yield {"type": "step", "text": title}
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
                                    max_tokens=prepared["profile"]["max_tokens"],
                                    temperature=0.3, history=prepared["history"])
                pieces.append(text)
                yield {"type": "delta", "text": text}
            else:
                for piece in stream(ASSISTANT_SYSTEM_PROMPT, prepared["prompt"],
                                    max_tokens=prepared["profile"]["max_tokens"],
                                    temperature=0.3, history=prepared["history"]):
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
        except Exception:      # noqa: BLE001 — сохранить написанное и отдать ошибку дальше
            # Оборвалась сама модель: llama-server упал, кончилась память,
            # разорвалось соединение. Обрыв браузера сохранял написанное, а
            # обрыв модели — терял, хотя терять тут ровно то же самое: пять
            # минут работы на длинном ответе. Ошибку не глотаем, она нужна
            # наверху, чтобы показать инженеру причину.
            if pieces:
                try:
                    self._finish(user, prepared, "".join(pieces), interrupted=True)
                except Exception:  # noqa: BLE001 — исходная причина важнее
                    pass
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

        # Профиль разговора: сколько заходов, сколько фрагментов, какой объём
        # ответа. Считаем один раз здесь — дальше все шаги берут его отсюда,
        # иначе половина работы шла бы в одном режиме, а половина в другом.
        profile = self._profile(chat)
        hits, trail = self._collect(chat, question, history,
                                    top_k or profile["top_k"],
                                    attachments=attachments, rounds=profile["rounds"])
        # Документы, названные в вопросе, сверяем с описью САМИ. Полагаться
        # на то, что поиск случайно вытянет нужный том, а модель случайно не
        # соврёт про его отсутствие, здесь нельзя: это ровно то место, где
        # помощник отвечал «RFC 4818 в библиотеке нет» о лежащем на полке
        # документе.
        mentioned_block, mentioned_ids = self._mentioned(question, history)
        pinned = self._pin_mentioned(hits, mentioned_ids, question)
        if pinned:
            hits = pinned + list(hits)
            for rank, hit in enumerate(hits, start=1):
                hit.rank = rank
        retriever = self.reports.get_retriever()
        # Половина библиотеки английская, а спрашивают по-русски. Если запрос
        # дополнен по двуязычному словарю — сказать об этом: иначе английский
        # фрагмент в источниках выглядит взявшимся ниоткуда. Заодно доносим
        # предупреждение поиска: падение службы эмбеддингов в чате раньше не
        # было видно вовсе, а поиск при этом работал вполсилы.
        expansion = list(getattr(retriever, "last_expansion", []) or [])
        warning = getattr(retriever, "last_warning", "") or ""

        case_block = self._case_block(chat)
        # Сколько окна остаётся файлам после вопроса, карточки письма,
        # разговора и обязательного пола под источники библиотеки.
        window = int(getattr(self.settings, "assistant_context_chars", 0) or 26000)
        spent = (len(question) + len(case_block)
                 + sum(len(item["content"]) for item in history)
                 + min(MIN_LIBRARY_CHARS, window))
        attachment_block, attachment_chars = self._attachment_block(
            attachments, room=max(0, window - spent))
        # Карта библиотеки: полки и документы. Без неё помощник знает только
        # то, что попало в найденные фрагменты, и не может ни отправить к
        # соседнему тому, ни честно сказать «по этой линии у нас ничего нет».
        catalog_block = self._catalog_block(chat, hits)
        # В окно модели идут не только фрагменты библиотеки. Разговор,
        # вопрос, карточка письма и приложенные файлы занимают то же самое
        # место, и раньше их никто не считал: бюджет соблюдался по одной
        # своей части, а промпт всё равно вылезал за окно.
        catalog_block, history = self._fit_reserved(
            catalog_block, history, attachment_chars,
            fixed=len(question) + len(case_block))
        history_chars = sum(len(item["content"]) for item in history)
        reserved = (attachment_chars + history_chars + len(question)
                    + len(case_block) + len(catalog_block))
        sources = self._build_sources(hits, reserved=reserved)
        documents = self._document_cards(sources)
        sources, documents = self._fit_window(
            sources, documents, reserved=reserved)
        prompt = ASSISTANT_PROMPT.format(
            question=question,
            case_block=case_block,
            attachments=attachment_block,
            catalog=catalog_block,
            mentioned=mentioned_block,
            library_map=_render_map(documents),
            sources=_render_sources(sources, documents),
            target_words=profile["target_words"],
        )
        return {
            "chat": chat,
            "profile": profile,
            "question": question,
            "question_message": question_message,
            "history": history,
            "hits": len(hits),
            "sources": sources,
            "documents": documents,
            "attachments": [item.to_dict() for item in attachments],
            "expansion": expansion,
            "warning": warning,
            "trail": [step.title() for step in trail],
            "prompt": prompt,
        }

    # -- сборка материала ---------------------------------------------------

    def _attachment_block(self, attachments: Sequence[Any],
                          *, room: int | None = None) -> tuple[str, int]:
        """Приложенные файлы для промпта и их вес в знаках.

        Дамп на десятки мегабайт в окно модели не поместится никогда, поэтому
        берём начало: там заголовки сессии и первые ошибки, по которым обычно
        и понятно, что случилось. Об обрезке говорим прямо — иначе модель
        сделает вывод «ошибок больше нет» по обрезанному хвосту.

        ``room`` — сколько знаков под файлы осталось от окна модели. Настройка
        assistant_attachment_chars задаёт желаемое, но окно сильнее: при
        вложении в 40 000 знаков и окне в 26 000 промпт вырастал до 47 678
        знаков, и llama.cpp молча выбрасывал его начало вместе с системной
        инструкцией. Обрезка при этом видна — в шапке файла стоит «показано
        N из M знаков».
        """
        if not attachments:
            return "", 0
        limit = int(getattr(self.settings, "assistant_attachment_chars", 0) or 8000)
        if room is not None:
            # Окно может ужать настройку, но не расширить её: маленькое
            # значение ставят осознанно, и «подрасти» ему нельзя.
            limit = min(limit, max(MIN_ATTACHMENT_CHARS, room))
        texts = [(item, (item.text or "").strip()) for item in attachments]
        shares = _share_chars([len(text) for _, text in texts], limit)
        blocks = []
        for (item, text), share in zip(texts, shares):
            if not text:
                blocks.append(
                    f"[Файл: {item.name}] текст извлечь не удалось"
                    + (f" — {item.note}" if item.note else "")
                )
                continue
            cut = len(text) > share
            body = text[:share].rstrip() + ("\n…(файл показан не целиком)" if cut else "")
            head = f"[Файл: {item.name}, {ATTACHMENT_TITLES.get(item.kind, item.kind)}"
            if cut:
                head += f", показано {share} из {len(text)} знаков"
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
            # как самостоятельный источник. Сверху стоит потолок, и он не
            # украшение: без него сосед дорожает вместе с самим фрагментом, и
            # четыре верхних попадания забирали своими соседями 8800 знаков —
            # больше трети окна. Вместо восьми целых фрагментов библиотеки
            # модель получала четыре и восемь их половинок, а семь найденных
            # документов не доходили до неё вовсе. Отсюда и скудный ответ.
            half = min(max(limit // 2, 300), NEIGHBOUR_CHARS)
            # У предыдущего фрагмента нужен ХВОСТ: он примыкает к найденному
            # куску и продолжается в нём. Обрезка с конца (как везде) оставила
            # бы дальний край и выбросила ровно то место, ради которого соседа
            # и брали, — начало таблицы, обрывающейся в найденном фрагменте.
            lead = _tidy_end(before, half) if before else ""
            tail = _tidy(after, half) if after else ""

            cost = len(text) + len(lead) + len(tail) + len(chunk.citation) + 40
            if sources and spent + cost > budget:
                # Пропускаем этот фрагмент, но выдачу не обрываем: следующий
                # может оказаться короче и в остаток окна ещё поместиться.
                # С «break» первый же неподошедший источник уносил с собой и
                # весь хвост — включая короткие фрагменты, которым места
                # хватало.
                continue
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

    def _fit_window(self, sources: List[Dict[str, Any]], documents: List[Dict[str, Any]],
                    *, reserved: int) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Последняя сверка с окном модели — уже по готовому тексту материала.

        Бюджет источников считается по их длине, но в промпт идут ещё
        оглавления документов и подписи фрагментов. На большой библиотеке
        оглавления — это тысячи знаков, и промпт вылезал за окно, ничего
        об этом не сообщая.

        Порядок отказа: сначала оглавления (самое необязательное — они лишь
        подсказывают, что ещё есть в документе), и только потом хвост
        подборки, где относимость уже низкая. Последний фрагмент остаётся
        всегда: без единого источника отвечать не на что.
        """
        window = int(getattr(self.settings, "assistant_context_chars", 0) or 26000)
        room = max(window - reserved, min(MIN_LIBRARY_CHARS, window))
        while sources:
            weight = len(_render_map(documents)) + len(_render_sources(sources, documents))
            if weight <= room:
                break
            if any(card.get("outline") for card in documents):
                for card in documents:
                    card["outline"] = []
                continue
            if len(sources) == 1:
                break
            sources = sources[:-1]
            documents = self._document_cards(sources)
        return sources, documents

    def _finish(self, user: User, prepared: Dict[str, Any], text: str,
                *, interrupted: bool = False) -> Dict[str, Any]:
        chat: Chat = prepared["chat"]
        sources: List[Dict[str, Any]] = prepared["sources"]
        labels = {item["label"] for item in sources}
        # Ссылки сверяем с подборкой. Модель иногда пишет [S9], когда в
        # подборке пять фрагментов: такая ссылка ведёт в никуда, и считать
        # её процитированным источником — врать инженеру в лицо. Ненайденные
        # метки в счётчик не идут и в разметке гасятся.
        used = _used_labels(text) & labels
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
                  # «Найдено» — это найденное поиском, а не уцелевшее после
                  # обрезки по окну модели. Раньше здесь стояло одно число,
                  # и молча выброшенные фрагменты выглядели ненайденными.
                  "found": int(prepared.get("hits") or len(sources)),
                  "shown": len(sources), "cited": len(used),
                  "documents": len(prepared.get("documents") or []),
                  "interrupted": interrupted},
        )
        if chat.title == DEFAULT_TITLE:
            self.repos.chats.rename(chat.id, _make_title(prepared["question"]))
        self.repos.audit.log(
            "chat.ask", user=user, object_type="chat", object_id=str(chat.id),
            details={"found": int(prepared.get("hits") or len(sources)),
                     "shown": len(sources), "cited": len(used)},
        )
        return {
            "question": prepared["question_message"].to_dict(),
            "answer": answer.to_dict(),
            "chat": self.get_chat(user, chat.id).to_dict(),
            "documents": prepared.get("documents") or [],
            "expansion": prepared.get("expansion") or None,
            "warning": prepared.get("warning") or None,
        }

    def _profile(self, chat: Chat) -> Dict[str, int]:
        """Насколько глубоко работать в этом разговоре.

        «Глубоко» — это ровно то, что стоит в настройках отдела: режим ничего
        не выдумывает, он только урезает. «Быстро» берёт от каждой настройки
        минимум с потолком из FAST_LIMITS, поэтому опустить настройку ниже
        быстрого режима по-прежнему можно — и быстрый не станет от этого
        медленнее глубокого.
        """
        глубоко = {
            "rounds": max(0, int(getattr(self.settings, "assistant_rounds", RESEARCH_ROUNDS))),
            "top_k": int(getattr(self.settings, "assistant_top_k", 0)
                         or self.settings.retrieval_top_k),
            "target_words": int(getattr(self.settings, "assistant_target_words", 0) or 500),
            "max_tokens": self._max_tokens(),
        }
        if chat_mode(chat) == "deep":
            return глубоко
        return {имя: min(значение, FAST_LIMITS[имя]) for имя, значение in глубоко.items()}

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

    def _fit_reserved(self, catalog_block: str, history: List[Dict[str, str]],
                      attachment_chars: int, *, fixed: int
                      ) -> tuple[str, List[Dict[str, str]]]:
        """Ужать всё, кроме фрагментов, чтобы промпт остался в окне модели.

        Материалу библиотеки гарантирован пол в MIN_LIBRARY_CHARS знаков —
        без источников отвечать не на что. Но пол этот односторонний: когда
        вложения, разговор и вопрос вместе занимали больше окна, источники
        упирались в пол, а промпт всё равно вылезал за границу. При вложении
        на 40 000 знаков замер давал промпт 47 678 знаков при
        assistant_context_chars = 26 000 — то есть «жёсткая граница» не
        держала ничего, и llama.cpp молча выбрасывал начало промпта вместе с
        системной инструкцией.

        Поэтому режем сверху и в понятном порядке: сперва карта библиотеки
        (полезная, но необязательная — от неё оставляем перечень полок),
        потом старые реплики разговора. Вложения и вопрос не трогаем: это
        то, о чём человек спросил, и молча его обрезать нельзя — у вложений
        для этого есть свой предел, assistant_attachment_chars.
        """
        window = int(getattr(self.settings, "assistant_context_chars", 0) or 26000)
        room = window - min(MIN_LIBRARY_CHARS, window)
        used = attachment_chars + fixed + sum(len(item["content"]) for item in history)

        if used + len(catalog_block) <= room:
            return catalog_block, history

        # Карта: сначала пробуем оставить её укороченной — одни полки без
        # названий документов уже отвечают на «есть ли у нас про это».
        spare = max(0, room - used)
        if spare < CATALOG_SHELVES_CHARS:
            catalog_block = ""
        elif len(catalog_block) > spare:
            catalog_block = self._catalog_block_at(spare)
        used += len(catalog_block)

        # Разговор: убираем самые старые реплики, оставляя последнюю пару.
        while used > room and len(history) > 2:
            used -= len(history[0]["content"])
            history = history[1:]
        return catalog_block, history

    def _catalog_block_at(self, limit: int) -> str:
        """Та же карта, но в заданное число знаков."""
        try:
            rows = self._catalog().rows()
        except Exception:              # noqa: BLE001 — карта не обязательна
            return ""
        return render_catalog(rows, domain_titles=self._domain_titles(), limit=limit)

    # -- разбор в несколько заходов ------------------------------------------

    def _rounds(self) -> int:
        """Сколько заходов разрешено. Ноль — разбор выключен, один поиск."""
        return max(0, int(getattr(self.settings, "assistant_rounds", RESEARCH_ROUNDS)))

    def _collect(self, chat: Chat, question: str, history: Sequence[Dict[str, str]],
                 top_k: int | None, *, attachments: Sequence[Any] = (),
                 on_step: "Callable[[Step], None] | None" = None,
                 rounds: int | None = None
                 ) -> tuple[List[Hit], List[Step]]:
        """Материал для ответа: первый поиск, а затем разбор заходами.

        Возвращает найденное и след разбора — что именно спрашивали. След
        показывается инженеру: он должен видеть, ЧТО помощник искал, иначе
        ответ «в библиотеке этого нет» невозможно ни проверить, ни оспорить.
        """
        first = self._search(chat, question, history, top_k, attachments=attachments)
        rounds = self._rounds() if rounds is None else max(0, int(rounds))
        if not rounds or not first:
            # Разбор выключен или библиотека ничего не дала: заходы по пустому
            # месту только сожгут время модели.
            return list(first), []

        rankings: List[List[Hit]] = [list(first)]
        pinned: List[Hit] = []
        seen = {hit.chunk.chunk_id for hit in first}
        trail: List[Step] = []
        catalog = self._catalog_block(chat, first)
        case_block = self._case_block(chat)

        for index in range(rounds):
            step = self._next_step(question, case_block, catalog,
                                   rankings, pinned, trail, rounds - index)
            if step is None or step.is_final:
                break
            trail.append(step)
            if on_step is not None:
                on_step(step)
            found, note = self._run_step(step, chat)
            fresh = [hit for hit in found if hit.chunk.chunk_id not in seen]
            seen.update(hit.chunk.chunk_id for hit in fresh)
            if step.kind == "читать":
                # Названный раздел инженер (в лице модели) попросил явно —
                # он обязан дойти до ответа, а не проиграть слияние рангов.
                pinned.extend(fresh)
            elif fresh:
                rankings.append(fresh)
            # У «читать» заметка своя — что именно открыли; счёт новых
            # фрагментов дописываем к ней, а не вместо неё.
            counted = found_note(len(fresh))
            step.note = f"{note}; {counted}" if note else counted

        return self._merge(rankings, pinned, top_k), trail

    def _merge(self, rankings: Sequence[Sequence[Hit]], pinned: Sequence[Hit],
               top_k: int | None) -> List[Hit]:
        """Сводит находки всех заходов в один список.

        Слияние по обратным рангам (RRF): шкалы BM25, косинуса и реранка
        между собой не сравнимы, а места в списках — сравнимы. Фрагмент,
        который всплыл в двух заходах по разным словам, поднимается выше —
        и это именно то, что нужно: два независимых способа его найти.
        """
        wanted = int(top_k or getattr(self.settings, "assistant_top_k", 0)
                     or self.settings.retrieval_top_k)
        lists = [list(item) for item in rankings if item]
        if not lists:
            merged: List[Hit] = []
        elif len(lists) == 1:
            merged = lists[0][:wanted]
        else:
            merged = reciprocal_rank_fusion(lists, top_k=wanted)
        # Прочитанное ставим первым и не даём вытеснить: его запросили по
        # имени, значит оно и есть ответ на «чего не хватало».
        head = list(pinned)
        known = {hit.chunk.chunk_id for hit in head}
        for hit in merged:
            if hit.chunk.chunk_id not in known:
                head.append(hit)
                known.add(hit.chunk.chunk_id)
        for rank, hit in enumerate(head, start=1):
            hit.rank = rank
        return head[:max(wanted, len(pinned))]

    def _next_step(self, question: str, case_block: str, catalog: str,
                   rankings: Sequence[Sequence[Hit]], pinned: Sequence[Hit],
                   trail: Sequence[Step], left: int) -> Step | None:
        """Спрашивает модель, что делать дальше. Не разобралось — конец разбора.

        Непонятый шаг намеренно не переспрашиваем: модель, которая не смогла
        написать одну строку по образцу, со второй попытки её обычно тоже не
        пишет, а инженер всё это время ждёт. Отвечаем по собранному.
        """
        llm = self.reports.get_llm()
        found = self._found_lines(rankings, pinned)
        prompt = RESEARCH_PROMPT.format(
            question=question,
            case_block=case_block,
            catalog=catalog,
            found=render_found(found),
            trail=render_trail(trail),
            left=left,
        )
        try:
            reply = llm.complete(RESEARCH_SYSTEM_PROMPT, prompt,
                                 max_tokens=RESEARCH_TOKENS, temperature=0.0)
        except Exception:              # noqa: BLE001 — разбор не обязателен
            # Модель не ответила: отвечаем по тому, что нашёл первый поиск.
            # Ронять из-за необязательного захода весь вопрос нельзя.
            return None
        return parse_step(reply)

    def _found_lines(self, rankings: Sequence[Sequence[Hit]],
                     pinned: Sequence[Hit]) -> List[Dict[str, Any]]:
        """Опись собранного для планировщика: метка, документ, раздел."""
        lines: List[Dict[str, Any]] = []
        seen: set = set()
        for hit in list(pinned) + [hit for group in rankings for hit in group]:
            uid = hit.chunk.chunk_id
            if uid in seen:
                continue
            seen.add(uid)
            lines.append({
                "label": f"S{len(lines) + 1}",
                "chunk_uid": uid,
                "citation": hit.chunk.citation,
                "doc_id": hit.chunk.doc_id,
            })
        return lines

    def _run_step(self, step: Step, chat: Chat) -> tuple[List[Hit], str]:
        """Выполняет шаг. Возвращает находки и замечание для следа."""
        if step.kind == "искать":
            return self._step_search(step, chat), ""
        if step.kind == "оглавление":
            return [], self._step_outline(step)
        if step.kind == "читать":
            return self._step_read(step)
        return [], ""

    def _step_search(self, step: Step, chat: Chat) -> List[Hit]:
        retriever = self.reports.get_retriever()
        if retriever is None:
            return []
        domains = [chat.domain] if chat.domain else None
        try:
            return retriever.search(step.argument, top_k=RESEARCH_TOP_K, domains=domains)
        except TypeError:              # поисковик без направлений
            return retriever.search(step.argument, top_k=RESEARCH_TOP_K)
        except Exception:              # noqa: BLE001 — заход не обязателен
            return []

    def _step_outline(self, step: Step) -> str:
        """Оглавление документа. Само по себе это не источник, а подсказка."""
        doc_id = self._resolve_document(step.argument)
        if not doc_id:
            return "документ не найден"
        try:
            found = self.repos.chunks.outline([doc_id], limit=OUTLINE_HEADINGS)
        except Exception:              # noqa: BLE001
            return "оглавление недоступно"
        headings = found.get(doc_id) or []
        return "; ".join(headings) if headings else "разделы не выделены"

    def _step_read(self, step: Step) -> tuple[List[Hit], str]:
        """Куски названного раздела документа и что именно открыли.

        Название модель пишет как умеет, а находим мы по совпадению — значит,
        открыть можем не то, что просили. След обязан назвать прочитанное:
        иначе инженер сверяет ответ с документом, которого никто не читал.
        """
        doc_id = self._resolve_document(step.argument)
        if not doc_id:
            return [], "документ не найден"
        document = self.repos.documents.by_doc_id(doc_id)
        if document is None:
            return [], "документ не найден"
        wanted = step.section.strip()
        note = ""
        try:
            if wanted:
                # Ищем раздел в БАЗЕ, а не в первых четырёхстах фрагментах,
                # поднятых в память: в книге на полторы тысячи фрагментов
                # двенадцатой главы там просто нет, и помощник отвечал
                # «раздела не нашёл» о разделе, который в документе есть.
                chunks = self.repos.chunks.find_sections(
                    document.id, wanted, limit=RESEARCH_READ_CHUNKS)
                if not chunks:
                    note = f"раздел «{step.section}» не найден, читаю с начала"
                    chunks = self.repos.chunks.for_document(
                        document.id, limit=RESEARCH_READ_CHUNKS)
            else:
                chunks = self.repos.chunks.for_document(
                    document.id, limit=RESEARCH_READ_CHUNKS)
        except Exception:              # noqa: BLE001
            return [], "документ не читается"
        hits = [Hit(chunk=chunk, score=0.0)
                for chunk in chunks[:RESEARCH_READ_CHUNKS]]
        opened = f"прочитано: {document.title or doc_id}"
        return hits, f"{opened}; {note}" if note else opened

    def _document_candidates(self, искомое: Sequence[str]):
        """Кого сличать с названным обозначением, в порядке пригодности.

        Сперва документы, у которых в названии или опознавателе стоит число из
        обозначения. Это важно по двум причинам. Число отсекает библиотеку до
        единиц строк, и перебор идёт по ним, а не по тридцати тысячам. И —
        главное — эта выборка идёт по ВСЕМ документам, а опись в подсказке
        отдаёт только действующие. Отменённая редакция стандарта в библиотеке
        лежит, и на вопрос о ней ответ «не числится» был бы ложью: у RFC
        отменённых редакций тысячи, и спрашивают о них постоянно.

        Потом — опись целиком: обозначение может быть и без числа.
        """
        число = next((часть for часть in искомое if часть.isdigit()), "")
        видели = set()
        if число:
            for документ in self.repos.documents.list(query=число,
                                                      limit=CANDIDATE_LIMIT):
                видели.add(документ.doc_id)
                yield документ.doc_id, документ.title or ""
        for row in self._catalog().rows():
            doc_id = str(row.get("doc_id") or "")
            if doc_id not in видели:
                yield doc_id, str(row.get("title") or "")

    def _mentioned(self, question: str, history: Sequence[Dict[str, str]]
                   ) -> tuple[str, List[str]]:
        """Названные в вопросе документы, сверенные с описью.

        «Есть ли у нас RFC 4818» — вопрос, на который в базе есть точный
        ответ. Раньше его добывали перебором фрагментов: поиск промахивался,
        выдача приходила пустая, и помощник со спокойной совестью отвечал, что
        такого документа в библиотеке нет. Документ при этом лежал на полке.

        Теперь обозначение из вопроса сверяется с описью напрямую, и модель
        получает не догадку, а факт: числится или не числится. Отвечать «в
        библиотеке этого нет» подсказка разрешает только по этой строке.
        """
        предыдущее = [item["content"] for item in history if item["role"] == "user"][-1:]
        обозначения = designations_in(" ".join([question, *предыдущее]))
        if not обозначения:
            return "", []
        строки: List[str] = []
        найденные: List[str] = []
        for имя in обозначения:
            doc_id = self._resolve_document(имя)
            документ = self.repos.documents.by_doc_id(doc_id) if doc_id else None
            if документ is None:
                строки.append(f"- {имя} — в библиотеке НЕ ЧИСЛИТСЯ")
                continue
            найденные.append(doc_id)
            пометка = "" if документ.status == "current" else f", {документ.status}"
            строки.append(
                f"- {имя} — ЧИСЛИТСЯ: «{документ.title or doc_id}» "
                f"({doc_id}, фрагментов {документ.chunk_count}{пометка})"
            )
        return ("\n### ДОКУМЕНТЫ, НАЗВАННЫЕ В ВОПРОСЕ\n"
                + "\n".join(строки) + "\n"), найденные

    def _pin_mentioned(self, hits: Sequence[Hit], doc_ids: Sequence[str],
                       question: str) -> List[Hit]:
        """Подложить фрагменты названного документа, если поиск его не принёс.

        Инженер спросил про конкретный документ — значит, читать надо именно
        его, а не пять соседних, которые всплыли по общим словам. Берём не
        начало документа, а те его фрагменты, которые ближе к вопросу.
        """
        уже = {hit.chunk.doc_id for hit in hits}
        добавка: List[Hit] = []
        for doc_id in doc_ids:
            if doc_id in уже:
                continue
            документ = self.repos.documents.by_doc_id(doc_id)
            if документ is None:
                continue
            куски = self.repos.chunks.for_document(документ.id, limit=PIN_SCAN_CHUNKS)
            if not куски:
                continue
            # statuses=None — фильтр по актуальности здесь снят намеренно.
            # Документ назван инженером поимённо, и если он помечен
            # заменённым, это повод сказать об этом в ответе, а не подсунуть
            # вместо нужного раздела первые попавшиеся страницы.
            подходящие = [найденное.chunk for найденное
                          in BM25Index(куски).search(question, top_k=PINNED_CHUNKS,
                                                     statuses=None)]
            for кусок in (подходящие or куски)[:PINNED_CHUNKS]:
                добавка.append(Hit(chunk=кусок, score=0.0))
            уже.add(doc_id)
        return добавка

    def _resolve_document(self, name: str) -> str:
        """Модель называет документ как умеет: идентификатором или названием.

        Сличать строки целиком нельзя: одно и то же обозначение пишут
        «RFC 4818», «RFC4818», «RFC-4818», «rfc4818.txt», а в названии
        документа стоит «RFC 4818. RADIUS Delegated-IPv6-Prefix Attribute».
        Прежнее опознание искало подстроку и потому узнавало только запись
        через пробел. Не узнав документ, помощник не открывал его вовсе — и
        отвечал, что такого в библиотеке нет, о документе, лежащем на полке.

        Сравниваем не строки, а последовательности слов и чисел. Заодно это
        разводит соседние номера: «RFC 481» — это ['rfc', '481'], и началом
        для ['rfc', '4818', ...] оно не является, тогда как сличение подстрок
        радостно отдавало под «RFC 481» документ RFC 4818.
        """
        wanted = str(name or "").strip().strip('«»"\'')
        if not wanted:
            return ""
        if self.repos.documents.by_doc_id(wanted) is not None:
            return wanted
        искомое = name_parts(wanted)
        if not искомое:
            return ""
        по_началу = ""
        по_вхождению = ""
        for doc_id, заголовок in self._document_candidates(искомое):
            if not doc_id:
                continue
            имя_файла = name_parts(doc_id.rsplit("/", 1)[-1])
            название = name_parts(заголовок)
            # Точное совпадение — с именем файла или с названием целиком.
            if искомое in (имя_файла, название, name_parts(doc_id)):
                return doc_id
            if по_началу:
                continue
            # Обозначение стоит в начале названия: «ГОСТ Р 53363» находит
            # «ГОСТ Р 53363-2009. Цифровые радиорелейные линии».
            if название[:len(искомое)] == искомое or имя_файла[:len(искомое)] == искомое:
                по_началу = doc_id
            elif not по_вхождению and _подряд(название, искомое):
                # Последнее средство: назвали кусок из середины названия.
                по_вхождению = doc_id
        return по_началу or по_вхождению

    def _catalog_block(self, chat: Chat, hits: Sequence[Hit]) -> str:
        """Карта библиотеки: полки с числами и названия документов.

        Полки, которых коснулся поиск, называются первыми: место в окне
        ограничено, а именно там лежит соседний том, до которого поиск не
        дотянулся. Разговор, привязанный к направлению, тоже поднимает своё.
        """
        limit = int(getattr(self.settings, "assistant_catalog_chars", 0) or CATALOG_CHARS)
        if limit <= 0:
            return ""
        try:
            rows = self._catalog().rows()
        except Exception:              # noqa: BLE001 — карта не обязательна
            return ""
        prefer: List[str] = []
        if chat.domain:
            prefer.append(chat.domain)
        for hit in hits:
            domain = str(getattr(hit.chunk, "domain", "") or "")
            if domain and domain not in prefer:
                prefer.append(domain)
        return render_catalog(
            rows, domain_titles=self._domain_titles(), prefer=prefer, limit=limit)

    def _catalog(self) -> LibraryCatalog:
        # Кэш живёт на службе отчётов: она одна на приложение, а помощник
        # создаётся на запрос.
        existing = getattr(self.reports, "_library_catalog", None)
        if existing is None:
            existing = LibraryCatalog(self.repos)
            setattr(self.reports, "_library_catalog", existing)
        return existing

    def _domain_titles(self) -> Dict[str, str]:
        """Русские названия направлений — те же, что видит человек."""
        try:
            from ..domains import registry  # noqa: PLC0415 — справочник не нужен при импорте

            found = registry(getattr(self.settings, "domains_path", None))
            return {domain.id: domain.title for domain in found.domains}
        except Exception:              # noqa: BLE001 — обойдёмся кодами направлений
            return {}

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
        # Метки показываем так, как их надо писать, — каждую в своей
        # скобке. Список через запятую модель принимала за образец и
        # отвечала «[S1, S2]».
        head += ". Фрагменты: " + " ".join(
            f"[{label}]" for label in card["labels"])
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
    # По очереди из каждого файла. Подряд нельзя: слова первого дампа
    # выбирали всю норму, и приложенный к тому же вопросу второй файл на
    # поиск не влиял вовсе — а прикладывают их как раз затем, чтобы
    # сопоставить одно с другим.
    queues: List[List[str]] = []
    for item in attachments:
        words = [
            word for word in re.split(r"[^0-9A-Za-zА-Яа-яЁё_.-]+", (item.text or "")[:4000])
            if len(word) >= 3
        ]
        if words:
            queues.append(words)

    seen: List[str] = []
    known = set()
    while queues and len(seen) < ATTACHMENT_KEYWORDS:
        for words in list(queues):
            word = None
            while words:
                candidate = words.pop(0)
                if candidate.lower() not in known:
                    word = candidate
                    break
            if word is None:
                queues.remove(words)
                continue
            known.add(word.lower())
            seen.append(word)
            if len(seen) >= ATTACHMENT_KEYWORDS:
                break
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
    """Метки источников, на которые ответ сослался.

    Разбор общий с остальной системой (reportgen.citations): «[S1, S2]» —
    такая же ссылка, как две отдельные, и считать её нулём нельзя. Раньше
    считалось: панель источников показывала первые три фрагмента подборки
    вместо процитированных, а рядом с ответом висело «ответ не опирается на
    библиотеку».
    """
    from ..citations import labels_in  # noqa: PLC0415 — модуль без зависимостей

    return labels_in(text)


def _tidy(text: str, limit: int) -> str:
    """Фрагмент для промпта и панели источников. См. corpus.tidy_quote."""
    from ..corpus import tidy_quote  # noqa: PLC0415 — не тянуть корпус при импорте

    return tidy_quote(text, limit)


def _tidy_end(text: str, limit: int) -> str:
    """То же, что :func:`_tidy`, но лишнее срезается спереди.

    Нужно ровно для одного случая — предыдущего соседа найденного фрагмента.
    """
    whole = _tidy(text, len(text or "") + 1)
    if len(whole) <= limit:
        return whole
    return "…" + whole[len(whole) - limit:].lstrip()


def _share_chars(sizes: Sequence[int], total: int) -> List[int]:
    """Разделить общий предел знаков между файлами.

    Поровну, но короткий файл не занимает чужого: то, что он не выбрал,
    достаётся длинным. Предел был на КАЖДЫЙ файл, и десять приложенных
    файлов выносили промпт за окно модели втрое — а переполнение окна
    llama.cpp не сообщает, он молча выбрасывает начало промпта вместе с
    системной инструкцией, и модель перестаёт ставить ссылки.
    """
    shares = [0] * len(sizes)
    pending = [i for i, size in enumerate(sizes) if size > 0]
    left = max(int(total), 0)
    while pending and left > 0:
        share = left // len(pending)
        if share <= 0:
            break
        modest = [i for i in pending if sizes[i] <= share]
        if not modest:
            for i in pending:
                shares[i] = share
            break
        for i in modest:
            shares[i] = sizes[i]
            left -= sizes[i]
        pending = [i for i in pending if i not in set(modest)]
    return shares


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _make_title(question: str) -> str:
    words = " ".join(question.split())
    title = words[:60].rstrip()
    if len(words) > 60:
        title = title.rsplit(" ", 1)[0] + "…"
    return title or DEFAULT_TITLE
