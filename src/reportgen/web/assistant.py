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
    DIGEST_PROMPT,
    DIGEST_SYSTEM_PROMPT,
    ASSISTANT_SYSTEM_PROMPT,
    ASSISTANT_TASK,
    ASSISTANT_TASK_SHORT,
    ASSISTANT_TITLE_PROMPT,
    NAME_PROMPT,
    NAME_SYSTEM_PROMPT,
    RESEARCH_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
)
from .. import parts
from ..retrieval import BM25Index, Hit, reciprocal_rank_fusion
from ..store.models import ATTACHMENT_TITLES, Chat, ChatMessage, User
from .catalog import (
    CATALOG_CHARS,
    CATALOG_SHELVES_CHARS,
    LibraryCatalog,
    render_catalog,
)
from ..designations import (
    MAX_DESIGNATIONS,  # noqa: F401 — читается снаружи как reportgen.web.assistant.MAX_DESIGNATIONS
    designations_in,
    implied_designations,
    name_parts,
)
from .research import (
    Step,
    found_note,
    parse_step,
    render_found,
    render_trail,
)
from .service import ReportService, ServiceError


#: Обороты, по которым видно, что спрашивают про НАШУ практику, а не про
#: устройство вещей. Список нарочно узкий: цена ошибки несимметрична. Принять
#: общий вопрос за частный — значит снова выдать разбор одного канала за норму,
#: а это ровно та беда, ради которой всё и заводится. Принять частный за общий
#: — значит показать на пару стандартов больше, отчёты при этом никуда не
#: деваются, просто стоят ниже.
_OWN_CASE_MARKERS = (
    "у нас", "у себя", "нам попадал", "нам встречал", "наш отчёт", "наши отчёт",
    "наших отчёт", "нашем отчёт", "в отчёте", "в отчётах", "по обращени",
    "по письму", "в письме", "прошлый раз", "в прошлый", "мы разбирал",
    "мы встречал", "мы писал", "мы делал", "мы наблюдал", "мы видел",
    "встречал ли", "встречали ли", "попадал ли", "попадали ли",
    "попадался ли", "попадалась ли", "попадалось ли", "попадались ли",
    "в каком отчёте", "в каких отчётах", "каком отчёте", "нам такой",
    "нам такое", "нам подобн", "у кого-то из наших",
    "был ли у нас", "были ли у нас", "бывал ли", "на этой линии",
    "на той линии", "на объекте", "на этом объекте", "раньше такое",
    "такое уже", "уже разбирал", "наши разбор", "наш разбор",
)


def asks_about_own_cases(text: str) -> bool:
    """Спрашивают ли про нашу практику, а не про устройство вещей.

    «Как устроено уплотнение E1» — вопрос об устройстве, и отвечать на него
    надо по нормам. «Встречали ли мы ИКМ-М в канальном интервале» — вопрос о
    нашей практике, и тут отчёты отдела и есть единственный верный источник.

    Различаем по оборотам, а не по одному слову «мы»: «как мы посчитаем
    занимаемую полосу» — обычный общий вопрос, и признаком «мы» тут ничего не
    значит. Слово «отчёт» само по себе тоже не признак: «по какому шаблону
    писать отчёт» — вопрос о нашей работе, но не о наших случаях.
    """
    строка = " " + " ".join(str(text or "").lower().replace("ё", "е").split()) + " "
    return any(маркер.replace("ё", "е") in строка for маркер in _OWN_CASE_MARKERS)


#: Откуда брать материал. Отдельный от режима ответа переключатель: инженер,
#: который разбирает новый сигнал, и инженер, который вспоминает, что мы уже
#: встречали похожее, спрашивают об одном и том же разными вопросами.
#:
#: «Везде» — обычная работа: нормы отвечают на «как устроено», отчёты
#: показывают, как это выглядело на практике, и стоят ниже.
#: «Нормы» — только стандарты, регламенты, литература и паспорта. Нужно, когда
#: ответ пойдёт в отчёт со ссылкой: сослаться там можно на норму, а не на
#: прошлый разбор.
#: «Отчёты» — только наши разборы. Это поиск по своей практике: «что мы уже
#: видели на этой линии».
CHAT_SOURCES = ("all", "norms", "reports")
DEFAULT_CHAT_SOURCES = "all"

#: Что считается нормой. Паспорт микросхемы и «прочее» сюда входят: это тоже
#: описание техники вообще, а не разбор одного случая.
NORM_DOC_TYPES = ("standards", "regulations", "literature", "datasheets", "misc")

SOURCE_DOC_TYPES = {
    "all": None,
    "norms": NORM_DOC_TYPES,
    "reports": ("reports",),
}


def chat_sources(chat: "Chat | None") -> str:
    """Откуда берём материал, с запасом на старые записи и мусор в поле."""
    выбор = str(getattr(chat, "sources", "") or "").strip().lower()
    return выбор if выбор in CHAT_SOURCES else DEFAULT_CHAT_SOURCES


def _пусто_по_вопросу(ответ: str) -> bool:
    """Модель честно сказала, что в этой части ничего нет.

    Такую выписку в промпт класть незачем: она занимает место и добавляет
    шум. Сверяем по началу ответа, а не по вхождению: «по вопросу здесь
    ничего нет, кроме упоминания полосы» — это уже содержательная выписка.
    """
    начало = " ".join(str(ответ or "").split()).lower().replace("ё", "е")[:60]
    return начало.startswith("по вопросу здесь ничего нет")


def _добавить_замечание(было: str, новое: str) -> str:
    """Два замечания об одном ответе — через точку, а не вместо друг друга."""
    первое = str(было or "").strip()
    второе = str(новое or "").strip()
    if not первое:
        return второе
    if not второе:
        return первое
    return f"{первое.rstrip('.')}. {второе}"


def _only_types(hits: Sequence[Hit], doc_types) -> List[Hit]:
    """Оставить только выбранные виды документов. Ничего не выбрано — всё."""
    if not doc_types:
        return list(hits)
    разрешено = set(doc_types)
    return [hit for hit in hits
            if str(getattr(hit.chunk, "doc_type", "")) in разрешено]


def prefer_norms(hits: Sequence[Hit]) -> List[Hit]:
    """Опустить лишние отчёты ниже норм, ничего не выбрасывая.

    Отдел столкнулся с этим на живой библиотеке: на вопрос «как устроено
    уплотнение E1» поиск поднял отчёт о разборе одного канала, где ИКМ-М был
    занят речью на арабском, — и частности того канала поехали в ответ как
    свойства E1 вообще. Отчёты при этом ценны: в них лучшие разборы конкретных
    сигналов. Поэтому не отсев, а порядок: сколько-то отчётов остаётся на своих
    местах, остальные уходят в хвост — и всё равно попадут в промпт, если
    заменить их нечем.

    Порядок выходит такой: сперва всё, что не отчёт, затем отчёты. Первая
    часть отвечает на вопрос «как устроено», вторая показывает, как это
    выглядело у нас. Отсева нет: отчёты доходят до промпта, если места хватает,
    и занимают его целиком, когда норм по теме нет вовсе — тогда перестановка
    не меняет ничего.

    Сколько отчётов в итоге увидит модель, решает бюджет окна: материал
    режется с хвоста (см. _build_sources). Это и есть нужная мера — на богатой
    нормами теме отчёты потеснятся сами, на бедной останутся все.

    Перестановка устойчивая: относительный порядок внутри обеих частей тот же,
    что дал поиск.
    """
    нормы: List[Hit] = []
    отчёты: List[Hit] = []
    for hit in hits:
        (отчёты if str(getattr(hit.chunk, "doc_type", "")) == "reports"
         else нормы).append(hit)
    return нормы + отчёты


def _проверить_вопрос(question: str) -> str:
    """Вопрос, годный к работе. Проверяется до всего остального.

    Отдельно от сбора материала, потому что нужно дважды: потоковый ответ
    кладёт вопрос в разговор и показывает его инженеру ДО сбора — сбор идёт
    минуты, и всё это время своего сообщения человек не видел.
    """
    question = (question or "").strip()
    if not question:
        raise ServiceError("пустой вопрос", 400)
    if len(question) > MAX_QUESTION:
        raise ServiceError(f"вопрос длиннее {MAX_QUESTION} символов", 400)
    return question


def _подряд(где: Sequence[str], что: Sequence[str]) -> bool:
    """Идёт ли ``что`` внутри ``где`` подряд, слово в слово."""
    if not что or len(что) > len(где):
        return False
    return any(list(где[i:i + len(что)]) == list(что)
               for i in range(len(где) - len(что) + 1))


#: Слова, которыми называют ПРИБАВКУ к документу, а не сам документ.
#: «G.703 Amendment 1» — это поправка на несколько страниц; «G.703» — сама
#: рекомендация на сто двадцать фрагментов. Спрашивая про G.703, инженер
#: имеет в виду второе.
_ПРИБАВКА = (
    "amendment", "amd", "corrigendum", "corr", "erratum", "addendum",
    "supplement", "annex", "appendix", "поправка", "изменение", "исправление",
    "дополнение", "приложение",
)


def _вес_совпадения(способ: int, заголовок: str) -> tuple:
    """Насколько хорошо документ подходит под названное обозначение.

    Подходящих в библиотеке несколько: у одной рекомендации МСЭ рядом лежат
    сама она, поправка к ней и исправление. Прежде побеждал ПЕРВЫЙ в описи, а
    опись отсортирована по названию — то есть выбор между рекомендацией и
    поправкой к ней решал алфавит.

    Отдел это и увидел: на вопрос про E1 система подложила «ITU-T Rec. G.703
    Amendment 1 (05/2021)» — поправку на 69 фрагментов вместо самой
    рекомендации. Ответ вышел ни о чём, а внизу стояла ссылка на поправку,
    и по ней инженер шёл смотреть то, чего там нет.

    Порядок предпочтений:

    1. обозначение в начале названия важнее, чем где-то в середине;
    2. сам документ важнее прибавки к нему;
    3. при равенстве — короткое название: «G.703» против «G.703 Amendment 1
       … Amendment 1», где обозначение просто повторено дважды.
    """
    низ = str(заголовок or "").casefold()
    прибавка = any(слово in низ for слово in _ПРИБАВКА)
    return (способ, 0 if прибавка else 1, -len(низ))


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

#: Сколько знаков приходится на токен, если в настройках не сказано иное.
#: Полтора — с запасом вниз для русского технического текста.
CHARS_PER_TOKEN = 1.5

#: Запас под то, чего в знаках не посчитать: служебные токены ролей, разметка
#: сообщений, расхождение оценки с настоящей токенизацией модели.
TOKEN_SAFETY = 512

#: Сколько отдельных проходов по не поместившемуся материалу делать. Каждый
#: проход — обращение к модели, и на машине отдела это секунды ожидания.
DIGEST_PASSES = 3
#: Сколько фрагментов отдавать модели за один такой проход.
DIGEST_CHUNK = 6
#: Насколько длинной должна быть одна выписка. Она идёт в итоговый промпт и
#: место там не бесплатно: триста слов — это около 2000 знаков.
DIGEST_WORDS = 300

#: Сколько документов подтягивать по ссылкам из найденного текста.
FOLLOW_REFS = 2

#: Сколько документов подкладывать ПО НАЗВАНИЮ на каждом вопросе
#: (:meth:`AssistantService._by_name`). Три — как у следования ссылкам:
#: больше, и подложенное по догадке начнёт вытеснять найденное поиском.
NAME_PASS_DOCS = 3

#: Перечитывать ли по заявке самого ответа (:meth:`_to_reread`). Один заход:
#: иначе во втором ответе модель назовёт третий документ, в третьем —
#: четвёртый, и вопрос будет обрабатываться до вечера.
REREAD = 1

#: Ответ на «в каких стандартах это описано» — строка обозначений через
#: запятую. Двести токенов: сотни хватало на сами обозначения, но не на
#: то, чем локальная модель их обычно предваряет («Это описано в …»), — и
#: строка обрывалась на первом же номере.
NAME_TOKENS = 200

#: Ответ ПРОСИТ достать документ. Отдел: «загрузите документы RFC и ITU-T,
#: которые уже лежат в библиотеке»; «в примечаниях пишет, что нет документа
#: ITU-T G.733, хотя вот он в библиотеке».
#:
#: Знать, чего в библиотеке нет, модель не может: тридцать тысяч документов,
#: а в окно уходит выборка описи на несколько десятков строк. Поэтому каждое
#: такое место — повод свериться с описью и, если документ на полке, сказать
#: об этом инженеру прямо под ответом.
_ПРОСИТ_ДОСТАТЬ = re.compile(
    r"загруз\w*|скача\w*|достать|раздоб\w*"
    r"|добав\w*\s+(?:его\s+|их\s+)?в\s+библиотек\w*"
    r"|(?:нет|отсутству\w*|не\s+числится|не\s+найден\w*)\s+в\s+библиотек\w*"
    r"|в\s+библиотек\w*\s+(?:нет|отсутству\w*)"
    # «Нужен стандарт ITU-T G.704» — так пишут чаще, чем «нужен документ».
    r"|(?:требу\w*|нужен|нужна|нужны|необходим\w*|не\s+хватает)\s+"
    r"(?:ещё\s+|еще\s+)?(?:документ\w*|стандарт\w*|рекомендаци\w*|"
    r"специфик\w*|описани\w*|rfc|гост\w*)",
    re.IGNORECASE,
)

#: Вес поиска по ОДНОМУ УЗЛУ состава при слиянии — против единицы у поиска
#: по самому вопросу. Узел спрашивали не мы, а справочник: он полезен,
#: когда вопрос про целое, но первое место по слову «антенна» не должно
#: стоить столько же, сколько первое место по всему вопросу инженера.
#:
#: Ноль,35 подобран по смыслу, а не на глаз: три узла, нашедшие один и тот
#: же документ, вместе перевешивают одну находку по вопросу (0,35 × 3 > 1
#: на первых местах), а один узел в одиночку — нет. Ровно это и нужно:
#: документ, всплывший у трёх узлов подряд, и есть узел тракта.
PART_WEIGHT = 0.35
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
               archived: bool | None = None, mode: str | None = None,
               sources: str | None = None) -> Chat:
        self.get_chat(user, chat_id)
        if mode is not None and mode not in CHAT_MODES:
            raise ServiceError(
                "неизвестный режим ответа '%s'; допустимы: %s"
                % (mode, ", ".join(CHAT_MODES)), 400)
        if sources is not None and sources not in CHAT_SOURCES:
            raise ServiceError(
                "неизвестный отбор источников '%s'; допустимы: %s"
                % (sources, ", ".join(CHAT_SOURCES)), 400)
        self.repos.chats.update(chat_id, domain=domain, archived=archived,
                                mode=mode, sources=sources)
        return self.get_chat(user, chat_id)

    def delete(self, user: User, chat_id: int) -> None:
        self.get_chat(user, chat_id)
        self.repos.chats.delete(chat_id)
        self.repos.audit.log("chat.delete", user=user, object_type="chat", object_id=str(chat_id))

    # -- ответ --------------------------------------------------------------

    def ask(self, user: User, chat_id: int, question: str, *,
            top_k: int | None = None,
            pinned: Sequence[str] = ()) -> Dict[str, Any]:
        """Полный ответ одним куском (без потоковой выдачи)."""
        prepared = self._prepare(user, chat_id, question, top_k=top_k,
                                 pinned=pinned)
        text = self._complete(prepared)
        добор = self._to_reread(text, prepared)
        if добор:
            prepared = self._prepare(user, chat_id, question, top_k=top_k,
                                     pinned=list(pinned) + добор,
                                     question_message=prepared["question_message"])
            text = self._complete(prepared)
        return self._finish(user, prepared, text)

    def _complete(self, prepared: Dict[str, Any]) -> str:
        return self.reports.get_llm().complete(
            ASSISTANT_SYSTEM_PROMPT, prepared["prompt"],
            max_tokens=prepared["profile"]["max_tokens"], temperature=0.3,
            history=prepared["history"],
        )

    def _to_reread(self, text: str, prepared: Dict[str, Any]) -> List[str]:
        """Документы, названные в ответе, лежащие на полке и НЕ прочитанные.

        Отдел: «не надо мне документы эти показывать, нужно чтобы модель сама
        всё видела, анализировала и давала ответ со ссылками на эти
        документы, я же для этого модель использую».

        Верно, и сноской под ответом это не лечится. Ответ, который сам
        признаётся, что ему нужен G.703, — это не ответ, а заявка на
        материал. Заявку исполняем: подкладываем названное и спрашиваем
        заново. Инженер получает один ответ, собранный по настоящим
        документам, и ссылки в нём ведут в них.

        Перечитываем ОДИН раз. Это не проверка внутри условия, а устройство
        самого хода: и ``ask``, и ``ask_stream`` спрашивают заново ровно один
        раз и больше к этому месту не возвращаются. Иначе разговор
        зациклится — во втором ответе модель назовёт третий документ, в
        третьем четвёртый, и вопрос будет обрабатываться до вечера.
        """
        if not int(getattr(self.settings, "assistant_reread", REREAD) or 0):
            return []
        # Прочитанным считаем только то, что дошло ТЕКСТОМ. Документ,
        # дошедший выпиской, модель видела в пересказе на триста слов — и
        # если ответ всё равно просит его, значит пересказа не хватило.
        # Перечитывание кладёт такой документ поимённо, и приходит он
        # фрагментами целиком: ровно то, чего ответу недостало.
        материал = list(prepared.get("sources") or [])
        прочитано = {str(item.get("doc_id") or "") for item in материал}
        # А вот документ, уже лежащий в материале текстом, во втором заходе
        # даст модели ровно то же самое — это будет не разбор, а вторая
        # попытка наугад.
        return [item["doc_id"] for item in self._named_in_answer(text, материал)
                if item["doc_id"] not in прочитано]

    def ask_stream(self, user: User, chat_id: int, question: str, *,
                   top_k: int | None = None,
                   pinned: Sequence[str] = ()) -> Iterator[Dict[str, Any]]:
        """Потоковый ответ: источники сразу, текст по мере генерации.

        Инженер видит, на чём основан ответ, ещё до того как модель дописала
        первое предложение — это заметно меняет ощущение от работы.
        """
        chat = self.get_chat(user, chat_id)
        question = _проверить_вопрос(question)
        question_message = self.repos.chats.add_message(
            chat.id, "user", question)
        # Своё сообщение инженер должен увидеть сразу, а не после сбора
        # материала: сбор идёт минуты, и всё это время экран был пуст.
        # Второй раз вопрос в разговор не кладётся: на перечитывании это же
        # сообщение передаётся сбору, и сбор своё добавление пропускает.
        yield {"type": "question", "message": question_message.to_dict()}

        def собрать(*, закреплённые, сообщение):
            """Сбор материала с рассказом о том, чем помощник сейчас занят."""
            поток = self._prepare_stream(
                user, chat_id, question, top_k=top_k, pinned=закреплённые,
                question_message=сообщение)
            while True:
                try:
                    этап = next(поток)
                except StopIteration as готово:
                    return готово.value
                yield {"type": "stage", "text": этап}

        prepared = yield from собрать(закреплённые=pinned,
                                      сообщение=question_message)
        показано = 0

        def обстановка():
            """Ход разбора и подборка — заново после перечитывания."""
            nonlocal показано
            # Ход разбора инженер должен видеть: ответ «в библиотеке этого
            # нет» без списка того, что искали, ни проверить, ни оспорить.
            следы = list(prepared.get("trail") or [])
            for title in следы[показано:]:
                yield {"type": "step", "text": title}
            показано = len(следы)
            yield {
                "type": "sources",
                # Вместе с выписками: их метки модель ставит в ответе
                # наравне, и если панель о них не знает, ссылка гаснет как
                # выдуманная.
                "sources": (list(prepared["sources"])
                            + list(prepared.get("digest_sources") or [])),
                "documents": prepared.get("documents") or [],
                "expansion": prepared.get("expansion") or None,
                "warning": prepared.get("warning") or None,
            }

        yield from обстановка()

        llm = self.reports.get_llm()
        pieces: List[str] = []
        stream = getattr(llm, "stream", None)
        # Между этой строкой и первым куском текста модель думает, а на
        # маленькой машине ещё и ждёт своей очереди: llama-server отвечает по
        # одному запросу. Без этой строки пауза выглядит зависанием.
        yield {"type": "stage", "text": "жду ответа модели"}
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

        # Ответ назвал документ, который лежит на полке и прочитан не был.
        # Это не ответ, а заявка на материал: исполняем её и спрашиваем
        # заново. Написанное до сих пор — черновик по неполному материалу, и
        # инженеру он не нужен; в разговоре останется только второй ответ.
        добор = self._to_reread("".join(pieces), prepared)
        if добор:
            названия = [элемент["title"] for элемент
                        in self._named_in_answer("".join(pieces),
                                                 prepared["sources"])]
            yield {"type": "step",
                   "text": "ответу не хватило документов — перечитываю: "
                           + "; ".join(названия[:3])}
            сообщение = prepared["question_message"]
            prepared = yield from собрать(
                закреплённые=list(pinned) + добор, сообщение=сообщение)
            pieces = []
            yield {"type": "restart"}
            yield from обстановка()
            yield {"type": "stage", "text": "пишу ответ заново"}
            if stream is None:
                text = self._complete(prepared)
                pieces.append(text)
                yield {"type": "delta", "text": text}
            else:
                for piece in stream(ASSISTANT_SYSTEM_PROMPT, prepared["prompt"],
                                    max_tokens=prepared["profile"]["max_tokens"],
                                    temperature=0.3, history=prepared["history"]):
                    pieces.append(piece)
                    yield {"type": "delta", "text": piece}

        result = self._finish(user, prepared, "".join(pieces))
        yield {"type": "done", **result}

    # -- внутреннее ---------------------------------------------------------

    def _prepare(self, user: User, chat_id: int, question: str,
                 *, top_k: int | None,
                 pinned: Sequence[str] = (),
                 question_message: ChatMessage | None = None) -> Dict[str, Any]:
        """Собрать материал под вопрос, не показывая ход работы."""
        поток = self._prepare_stream(
            user, chat_id, question, top_k=top_k, pinned=pinned,
            question_message=question_message)
        while True:
            try:
                next(поток)
            except StopIteration as готово:
                return готово.value

    def _prepare_stream(self, user: User, chat_id: int, question: str,
                        *, top_k: int | None,
                        pinned: Sequence[str] = (),
                        question_message: ChatMessage | None = None):
        """То же, но с рассказом о том, чем помощник занят прямо сейчас.

        Отдел: «добавь, чтобы был виден процесс — что делает в данный момент
        модель, в очереди ответа или что». Справедливо: сбор материала идёт
        минуты, и всё это время на экране не происходило ничего. Хуже того,
        именно здесь помощник дважды обращается к модели — за обозначениями
        и за выписками, — и пауза выглядела зависанием.

        Выдаёт строки этапов, возвращает собранное. Обычные вызовы ходят
        через :meth:`_prepare` и этапов не видят.

        ``pinned`` — документы, которые велено прочитать поимённо: так
        работает перечитывание по заявке самого ответа. Подкладываются они
        на тех же правах, что и названные в самом вопросе.
        """
        chat = self.get_chat(user, chat_id)
        question = _проверить_вопрос(question)

        history = [
            {"role": message.role, "content": _clip(message.content, HISTORY_CHARS)}
            for message in self.repos.chats.tail(chat.id, HISTORY_DEPTH)
        ]
        # На перечитывании вопрос в разговор не добавляем второй раз: он
        # задан один раз, и в переписке должен стоять один раз.
        if question_message is None:
            question_message = self.repos.chats.add_message(
                chat.id, "user", question)

        # Вложения привязываем к отправленному вопросу: в разговоре видно,
        # с какими файлами он был задан.
        attachments = self.repos.chats.attachments(chat.id, pending_only=True)
        if attachments:
            self.repos.chats.bind_attachments(chat.id, question_message.id)

        # Профиль разговора: сколько заходов, сколько фрагментов, какой объём
        # ответа. Считаем один раз здесь — дальше все шаги берут его отсюда,
        # иначе половина работы шла бы в одном режиме, а половина в другом.
        profile = self._profile(chat)
        yield "ищу по библиотеке"
        hits, trail = self._collect(chat, question, history,
                                    top_k or profile["top_k"],
                                    attachments=attachments, rounds=profile["rounds"])
        # Отчёт отдела — свидетельство об одном канале в один день, а не
        # общее правило. На общем вопросе он не должен вытеснять нормы;
        # на вопросе о нашей практике — наоборот, он и есть ответ.
        # Выбрал человек — перестановка ничего не меняет: в выдаче тогда
        # документы одного вида, и делить их не на что.
        if not asks_about_own_cases(question):
            hits = prefer_norms(hits)

        # Документы, названные в вопросе, сверяем с описью САМИ. Полагаться
        # на то, что поиск случайно вытянет нужный том, а модель случайно не
        # соврёт про его отсутствие, здесь нельзя: это ровно то место, где
        # помощник отвечал «RFC 4818 в библиотеке нет» о лежащем на полке
        # документе.
        yield (f"нашлось фрагментов: {len(hits)} "
               f"в {len({hit.chunk.doc_id for hit in hits})} док.")
        mentioned_block, mentioned_ids = self._mentioned(question, history)
        # Названное поимённо: перечитывание по заявке самого ответа.
        for doc_id in pinned:
            if doc_id and doc_id not in mentioned_ids:
                mentioned_ids = list(mentioned_ids) + [doc_id]
        # Поиск вернул пусто или почти пусто — спрашиваем у модели, КАК
        # называется нужный документ, и ищем его по описи по имени.
        yield "уточняю у модели, в каких стандартах это описано"
        по_имени, имена_след = self._by_name(hits, question, известные=mentioned_ids)
        if по_имени:
            mentioned_ids = list(mentioned_ids) + по_имени
        pinned = self._pin_mentioned(hits, mentioned_ids, question)
        if pinned:
            hits = pinned + list(hits)
            for rank, hit in enumerate(hits, start=1):
                hit.rank = rank
        # Ссылки ВНУТРИ найденного. «Чем G.733 отличается от G.734» — вопрос,
        # на который сами эти рекомендации отвечают наполовину: байтовую
        # структуру цикла обе описывают ссылкой на G.704. Без неё ответ
        # получится словесным пересказом отличий без единой цифры.
        yield "иду по ссылкам внутри найденного"
        по_ссылкам, ссылки_след = self._follow_refs(hits, question,
                                                    известные=mentioned_ids)
        if по_ссылкам:
            hits = list(hits) + по_ссылкам
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
        window = self._context_chars()
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
        sources, за_бюджетом = self._build_sources(hits, reserved=reserved)
        documents = self._document_cards(sources)
        sources, documents, снятые = self._fit_window(
            sources, documents, reserved=reserved)
        # Всё, что не поместилось на любом из шагов, идёт в разбор частями.
        # Прежде считался только последний шаг, а отброшенное бюджетом знаков
        # исчезало молча — и ответ собирался по половине найденного.
        мимо = list(за_бюджетом) + list(снятые)

        digest_block = ""
        task_block = ASSISTANT_TASK

        def собрать(куски: List[Dict[str, Any]],
                    карточки: List[Dict[str, Any]]) -> str:
            return ASSISTANT_PROMPT.format(
                question=question,
                case_block=case_block,
                attachments=attachment_block,
                catalog=catalog_block,
                mentioned=mentioned_block,
                library_map=_render_map(карточки),
                sources=_render_sources(куски, карточки),
                digest=digest_block,
                task=task_block.format(target_words=profile["target_words"]),
                target_words=profile["target_words"],
            )

        # Последняя проверка — по СОБРАННОМУ промпту, а не по его частям.
        # Всё, что выше, считает части: вопрос, карточку, историю, материал.
        # А в окно уходит ещё системная инструкция, шапки самого шаблона и
        # блок «документы, названные в вопросе» — их не считал никто, и
        # промпт вылезал за окно при формально соблюдённом бюджете.
        #
        # Место под выписки держим ЗАРАНЕЕ: они пойдут в тот же промпт, и
        # добавлять их «по остаточному принципу» значит снова его переполнить.
        запас = self._digest_room()
        sources, documents, prompt, отброшено = self._fit_tokens(
            sources, documents, собрать, history=history,
            answer_tokens=profile["max_tokens"], extra=запас)
        отброшено = list(отброшено) + мимо

        trail_lines = ([step.title() for step in trail]
                       + list(имена_след) + list(ссылки_след))
        # Фрагменты, попавшие в выписки. Текста их в промпте нет, но метки
        # есть, и модель ссылается на них наравне: значит, они такой же
        # источник ответа, как и остальные, и в панели должны быть.
        выписанные: List[Dict[str, Any]] = []
        if отброшено:
            # Не поместившееся не выбрасываем, а прочитываем отдельными
            # проходами: отдел просил все данные, и «не влезло» — не повод
            # молча ответить по половине найденного.
            yield (f"читаю частями то, что не поместилось в окно: "
                   f"{len(отброшено)} фрагм.")
            digest_block, выписанные = self._digest(
                отброшено, question, trail=trail_lines, room=запас)
            sources, documents, prompt, ещё = self._fit_tokens(
                sources, documents, собрать, history=history,
                answer_tokens=profile["max_tokens"])
            warning = _добавить_замечание(
                warning, self._digest_note(отброшено, digest_block, ещё))

        # ПОРЯДОК УСТУПОК, когда окно совсем тесное. Резать выдачу дальше
        # нельзя — последний фрагмент остаётся всегда, — поэтому уступает то,
        # без чего ответ хуже, но возможен:
        #
        #   1. подробное задание — четыре с половиной тысячи знаков порядка
        #      работы. Уступает ПЕРВЫМ: это инструкция, а не данные. Ответ от
        #      короткого задания выйдет менее развёрнутым, но останется
        #      верным — ссылки, запрет брать числа по памяти, требование
        #      выписывать перечни целиком и граница между «нет в найденном»
        #      и «нет в библиотеке» в коротком задании тоже есть;
        #   2. карта библиотеки — она подсказывает, ГДЕ ещё искать, но сама
        #      по себе ответа не содержит;
        #   3. выписки — пересказ, а не первоисточник, но всё же МАТЕРИАЛ:
        #      отдел просил все данные, и уступать им раньше инструкции
        #      значило бы отвечать по половине найденного ради подробного
        #      объяснения, как отвечать;
        #   4. сверка названных документов с описью — короткая, но именно она
        #      даёт право сказать «в библиотеке этого нет».
        #
        # Без этого порядка бюджет упирался в пол MIN_LIBRARY_CHARS и начинал
        # врать: на окне в 9000 токенов он обещал источникам 6000 знаков при
        # настоящих 4300, и промпт вылезал за окно, сколько ни режь выдачу.
        for уступка in ("задание", "карта", "выписки", "сверка"):
            if not self._too_big(prompt, history, profile["max_tokens"]):
                break
            if уступка == "задание" and task_block is ASSISTANT_TASK:
                task_block = ASSISTANT_TASK_SHORT
                warning = _добавить_замечание(
                    warning, "окно модели тесное — подробный порядок работы в "
                             "него не поместился, ответ будет менее развёрнутым")
            elif уступка == "выписки" and digest_block:
                digest_block = ""
                # Меток выписок в промпте больше нет — значит, и в панели
                # источников им не место: иначе панель обещает инженеру то,
                # чего модель не видела.
                выписанные = []
                warning = _добавить_замечание(
                    warning, "выписки не поместились в окно модели и в ответ "
                             "не вошли")
            elif уступка == "карта" and catalog_block:
                catalog_block = ""
                warning = _добавить_замечание(
                    warning, "карта библиотеки не поместилась в окно модели — "
                             "подсказать, где ещё искать, помощник не сможет")
            elif уступка == "сверка" and mentioned_block:
                mentioned_block = ""
            else:
                continue
            prompt = собрать(sources, documents)

        return {
            "chat": chat,
            "profile": profile,
            "question": question,
            "question_message": question_message,
            "history": history,
            "hits": len(hits),
            "sources": sources,
            "digest_sources": выписанные,
            "documents": documents,
            "attachments": [item.to_dict() for item in attachments],
            "expansion": expansion,
            "warning": warning,
            "trail": trail_lines,
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

        Бюджет. Материал обрезается по выведенному из окна модели пределу —
        иначе llama.cpp либо отвечает ошибкой, либо молча выбрасывает начало
        промпта вместе с системной инструкцией, и модель перестаёт ставить
        ссылки. Обрезаем с конца выдачи: там уже хвост относимости.

        Возвращаем ДВА списка: поместившееся и то, что за бюджет не влезло.
        Второе не выбрасывается — оно идёт в разбор частями (``_digest``).
        """
        if not hits:
            return [], []

        # Приложенные файлы уже заняли часть окна — остаток идёт библиотеке.
        # Совсем без источников не оставляем: отвечать будет не на что даже
        # по нормам, а именно за этим помощника и спрашивают. Но и выше
        # настройки не поднимаемся: если окно модели маленькое и это задано
        # осознанно, порог его не отменяет.
        budget = self._context_chars()
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
        за_бюджетом: List[Dict[str, Any]] = []
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
            тесно = bool(sources) and spent + cost > budget
            if not тесно:
                spent += cost

            # Не поместившееся не теряется: оно помечается «за бюджетом» и
            # уходит в разбор частями. Прежде такой фрагмент просто исчезал —
            # и ответ собирался по половине найденного молча.
            (за_бюджетом if тесно else sources).append({
                "label": "",
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
        # Метки идут сквозной нумерацией по всему найденному: по [S12]
        # инженер откроет источник и тогда, когда тот попал не в текст
        # промпта, а в выписку.
        for номер, item in enumerate(sources + за_бюджетом, start=1):
            item["label"] = f"S{номер}"
        return sources, за_бюджетом

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
                    *, reserved: int
                    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]],
                               List[Dict[str, Any]]]:
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
        window = self._context_chars()
        room = max(window - reserved, min(MIN_LIBRARY_CHARS, window))
        снятые: List[Dict[str, Any]] = []
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
            снятые.insert(0, sources[-1])
            sources = sources[:-1]
            documents = self._document_cards(sources)
        return sources, documents, снятые

    def _finish(self, user: User, prepared: Dict[str, Any], text: str,
                *, interrupted: bool = False) -> Dict[str, Any]:
        chat: Chat = prepared["chat"]
        sources: List[Dict[str, Any]] = prepared["sources"]
        # МАТЕРИАЛ — это всё, что модель получила: и фрагменты целиком, и те,
        # что дошли до неё выписками. Метки у них общие и сквозные, значит и
        # ссылка на любую из них законна.
        выписанные: List[Dict[str, Any]] = list(prepared.get("digest_sources") or [])
        материал = list(sources) + выписанные
        labels = {item["label"] for item in материал}
        # Ссылки сверяем с материалом. Модель иногда пишет [S9], когда в
        # подборке пять фрагментов: такая ссылка ведёт в никуда, и считать
        # её процитированным источником — врать инженеру в лицо. Ненайденные
        # метки в счётчик не идут и в разметке гасятся.
        used = _used_labels(text) & labels
        # В панель идёт ВЕСЬ материал, а процитированное помечается.
        #
        # Прежде здесь оставались только процитированные источники, и отдел
        # это заметил: «сначала источников было несколько, в конце остался
        # только один, как это работает и почему до сих пор не понятно».
        # Панель схлопывалась молча, и проверить ответ становилось нечем:
        # инженер видел, на что модель сослалась, но не видел, что ей дали.
        # А для проверки отчёта важно ровно второе — что модель прочитала и
        # ПРОМОЛЧАЛА. Признак «процитирован» отвечает на оба вопроса сразу.
        выписки_меток = {item["label"] for item in выписанные}
        kept = []
        for item in материал:
            # Соседние куски нужны были модели, в панель они не идут:
            # инженер открывает документ целиком одним нажатием.
            строка = {k: v for k, v in item.items() if k not in ("lead", "tail")}
            строка["cited"] = item["label"] in used
            строка["digest"] = item["label"] in выписки_меток
            kept.append(строка)
        answer = self.repos.chats.add_message(
            chat.id, "assistant", text.strip(),
            sources=kept,
            meta={"model": getattr(self.reports.get_llm(), "name", "unknown"),
                  # «Найдено» — это найденное поиском, а не уцелевшее после
                  # обрезки по окну модели. Раньше здесь стояло одно число,
                  # и молча выброшенные фрагменты выглядели ненайденными.
                  "found": int(prepared.get("hits") or len(sources)),
                  # «Видела целиком» и «дошло выпиской» — разные вещи, и
                  # смешивать их в одно число нельзя: по выписке инженер
                  # проверяет не текст, а чужой пересказ текста.
                  "shown": len(sources), "digested": len(выписанные),
                  "cited": len(used),
                  "documents": len(prepared.get("documents") or []),
                  # Документы, названные в САМОМ ответе и лежащие на полке.
                  "present": self._named_in_answer(text, материал),
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

    def _named_in_answer(self, text: str, материал: Sequence[Dict[str, Any]]
                         ) -> List[Dict[str, Any]]:
        """Документы, названные в ОТВЕТЕ и лежащие в библиотеке.

        Отдел, вопрос по SDH: «опять внизу: загрузите документы RFC и ITU-T,
        которые уже лежат в библиотеке». И следом: «название конкретных
        документов он даёт, и они лежат в библиотеке, но он требует их
        загрузить».

        Корень был в самой инструкции помощнику: правило 3 велело сказать,
        «какой документ стоит загрузить в библиотеку». Модель это и делала —
        а знать, чего в библиотеке НЕТ, она не может: тридцать тысяч
        документов, в окно уходит выборка описи на несколько десятков строк.
        Инструкцию поправили, но одной инструкции мало: модель называет
        документ и без прямого совета — «см. G.707», — и проверить это может
        только система.

        Поэтому обозначения из ответа сверяются с описью так же, как
        обозначения из вопроса, и найденное показывается инженеру под
        ответом: вот они, открывайте, доставать ничего не нужно.

        Два случая, и оба сюда попадают:

        * документа не было в материале — модель назвала то, чего не читала,
          и подсказать, что оно на полке, полезно всегда;
        * ответ ПРОСИТ его достать. Тогда называем и прочитанное: требовать
          загрузить документ, который сам же и процитировал, — ошибка грубее
          первой, и прятать её за «оно и так в панели» нельзя.

        Вне этих двух случаев молчим: ссылка на документ по ходу разбора —
        обычное дело, и строка под каждым ответом утопит полезное в шуме.
        """
        просят = bool(_ПРОСИТ_ДОСТАТЬ.search(text or ""))
        уже = set() if просят else {
            str(item.get("doc_id") or "") for item in материал}
        найдено: List[Dict[str, Any]] = []
        видели: set[str] = set()
        for имя in designations_in(text):
            doc_id = self._resolve_document(имя)
            if not doc_id or doc_id in уже or doc_id in видели:
                continue
            документ = self.repos.documents.by_doc_id(doc_id)
            if документ is None:
                continue
            видели.add(doc_id)
            найдено.append({
                "designation": имя,
                "doc_id": doc_id,
                "title": документ.title or doc_id,
                "chunks": документ.chunk_count,
            })
        return найдено

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
        doc_types = SOURCE_DOC_TYPES.get(chat_sources(chat))
        # Для разговора берём больше фрагментов, чем для отчёта: там материал
        # ограничен факт-пакетом, здесь — только вопросом. Лишнее всё равно
        # отсечёт бюджет окна в _build_sources.
        wanted = top_k or int(
            getattr(self.settings, "assistant_top_k", 0) or self.settings.retrieval_top_k
        )
        try:
            найдено = retriever.search(query, top_k=wanted, domains=domains,
                                       doc_types=doc_types)
        except TypeError:
            # Поисковик без поддержки направлений (лексический запасной вариант).
            найдено = retriever.search(query, top_k=wanted)
        # Отбор источников держим и здесь. Запасной поисковик ключа doc_types
        # не понимает и возвращает всё подряд — а человек, выбравший «нормы»,
        # получил бы отчёты и не узнал бы об этом.
        return _only_types(найдено, doc_types)

    def _follow_refs(self, hits: Sequence[Hit], question: str, *,
                     известные: Sequence[str] = ()
                     ) -> tuple[List[Hit], List[str]]:
        """Подтянуть документы, на которые ссылаются НАЙДЕННЫЕ фрагменты.

        Отдел: «я задаю вопрос, в чём разница стандарта G.733 от G.734.
        Помимо словесного описания в файлах, существует ещё и ссылка в этих
        документах на документ G.704, где для каждого описана байтовая
        структура. Сможет ли модель это всё проанализировать?»

        Прежде — нет. Обозначение из ВОПРОСА система сверяла с описью и
        документ подкладывала, а обозначение из НАЙДЕННОГО ТЕКСТА не читал
        никто. Ответ выходил словесным пересказом отличий без единой цифры:
        сами G.733 и G.734 байтовую структуру не приводят, они на неё
        ссылаются. Дотянуться до G.704 мог только сам планировщик разбора —
        если догадается и если остался заход.

        Теперь это делается само и без обращения к модели: в тексте найденных
        фрагментов ищутся обозначения, сверяются с описью и подкладываются
        те, которых в выдаче ещё нет. Правило то же, по которому работает
        инженер: увидел ссылку на норму — открыл норму.

        Граница одна и жёсткая: не больше ``assistant_follow_refs``
        документов — иначе одна рекомендация МСЭ утащит за собой двадцать.
        Обозначения собираются в порядке выдачи, поэтому первыми в предел
        попадают ссылки из самых относимых фрагментов. Шаг только один: по
        ссылке из ссылки не ходим, потому что подложенное по ссылке в этот
        обход уже не просматривается.
        """
        предел = int(getattr(self.settings, "assistant_follow_refs",
                             FOLLOW_REFS) or 0)
        # Ноль отсекает счёт взятого ниже; второй проверки здесь нет по той
        # же причине, что и у разбора частями.
        if not hits:
            return [], []

        названо: List[str] = []
        for hit in hits:
            текст = f"{hit.chunk.breadcrumbs}\n{hit.chunk.text}"
            for обозначение in designations_in(текст):
                if обозначение not in названо:
                    названо.append(обозначение)

        добавка: List[Hit] = []
        след: List[str] = []
        взято: List[str] = []
        for обозначение in названо:
            if len(взято) >= предел:
                break
            doc_id = self._resolve_document(обозначение)
            if not doc_id:
                continue
            # Уже найденное второй раз не подкладывается: _pin_mentioned
            # сам пропускает документы, которые в выдаче уже есть, и вернёт
            # пустой список. Повторять эту проверку здесь незачем.
            куски = self._pin_mentioned(list(hits) + добавка, [doc_id], question)
            if not куски:
                continue
            взято.append(обозначение)
            добавка.extend(куски)
            след.append(f"по ссылке в найденном открыл {обозначение}")
        return добавка, след

    def _by_name(self, hits: Sequence[Hit], question: str,
                 *, известные: Sequence[str]) -> tuple[List[str], List[str]]:
        """Поиск ничего не дал — спросить у модели, КАК называется документ.

        Отдел: «вновь спрашиваю, что такое E1 уплотнение и его структура, в
        конце пишет, что нет стандарта ITU-T G.703; проверяю в библиотеке —
        он конечно же есть и на месте».

        Замер по этому вопросу: поиск вернул НОЛЬ фрагментов при G.703 и
        G.704 на полке. Причина не в обозначениях — их в вопросе нет вовсе.
        Вопрос русский и словесный («уплотнение», «структура»), документы
        английские («hierarchical digital interfaces», «frame structures»),
        и общих слов у них попросту нет. Двуязычный словарь такие места
        закрывает по одному, но закрыть все не может: словарь конечен, а
        способов спросить — бесконечно.

        Зато нужный документ модель НАЗЫВАЕТ сама — в том же ответе, где
        объявляет его отсутствующим. Это и есть недостающий поиск: не по
        словам вопроса, а по имени документа. Спрашиваем обозначения одной
        короткой репликой, сверяем с описью и подкладываем то, что нашлось.
        Отвечает опись, а не модель: чего в библиотеке нет, то и не придёт.

        Заход делается на КАЖДЫЙ вопрос, а не только на вопрос без номеров.
        Сначала он включался лишь там, где имени взять неоткуда, — и этого
        мало: инженер спрашивает «чем G.733 отличается от G.734», а байтовая
        структура цикла лежит в G.704, которую он не называл. Сверка с
        описью знает только то, что напечатано в вопросе; знание о том,
        КАКИЕ ЕЩЁ документы к делу относятся, есть только у модели.

        Мерить вместо этого «мало ли нашлось» бесполезно, и это замерено. По
        вопросу про E1 поиск вернул ПЯТЬ документов — G.704, G.732, G.733,
        G.734 и учебник, — и ни один порог по объёму выдачи такую выдачу
        плохой не назовёт. А главного, G.703, в ней нет. Полнота выдачи не
        говорит о её верности, и ждать от неё этого не надо.

        Цена — одно короткое обращение к модели (сотня токенов) на вопрос.
        Против трёх проходов выписок и ответа на четыре тысячи токенов это
        немного, но ноль в настройке заход выключает.
        """
        предел = int(getattr(self.settings, "assistant_name_pass", NAME_PASS_DOCS))
        if предел <= 0:
            return [], []
        llm = self.reports.get_llm()
        if llm is None:
            return [], []
        try:
            ответ = llm.complete(
                NAME_SYSTEM_PROMPT,
                NAME_PROMPT.format(question=question, limit=MAX_DESIGNATIONS),
                max_tokens=NAME_TOKENS, temperature=0.0)
        except Exception:              # noqa: BLE001 — заход не обязателен
            return [], ["поиск по названию: модель не ответила"]

        добавка: List[str] = []
        названо: List[str] = []
        for имя in designations_in(ответ or ""):
            if len(добавка) >= предел:
                break
            doc_id = self._resolve_document(имя)
            if not doc_id or doc_id in известные or doc_id in добавка:
                continue
            if self.repos.documents.by_doc_id(doc_id) is None:
                continue
            добавка.append(doc_id)
            названо.append(имя)
        if not добавка:
            return [], []
        return добавка, [f"слова вопроса до библиотеки не дотянулись — "
                         f"нашёл по названию: {', '.join(названо)}"]

    def _digest(self, dropped: Sequence[Dict[str, Any]], question: str,
                *, trail: List[str], room: int = 0
                ) -> tuple[str, List[Dict[str, Any]]]:
        """Выписки из материала, не поместившегося в окно, — по частям.

        Отдел: «нужны все данные, пусть разбиваются на части». И это верно:
        отбросить найденное — значит ответить по половине библиотеки, ничем
        не показав, что вторая половина была и её не читали.

        Поэтому лишнее не выбрасывается, а прочитывается отдельными
        проходами. В каждом проходе модель получает часть фрагментов и
        выписывает из неё только то, что относится к вопросу, сохраняя метки
        [S12] — по ним инженер откроет тот же источник, что и всегда. На
        последнем проходе ответ собирается по полному материалу: по тексту
        того, что влезло, и по выпискам из того, что не влезло.

        Цена честная и её надо знать: каждый проход — отдельное обращение к
        модели. Поэтому проходов не больше ``assistant_digest_passes``, а
        ноль выключает разбор частями совсем — тогда лишнее отбрасывается,
        как раньше, но об этом по-прежнему говорится словами.

        ``room`` — сколько знаков отведено выпискам в промпте. Выйти за него
        нельзя: иначе выписки вытолкнут сами источники, ради которых всё и
        затевалось. Ноль — не ограничивать.

        Возвращает выписки и ТЕ фРАГМЕНТЫ, которые в них вошли. Второе не
        мелочь: их метки модель ставит в ответе наравне с остальными, и без
        этого списка интерфейс считал такую ссылку выдуманной и перечёркивал
        её — отдел это увидел («некоторые ссылки перечёркнуты»).
        """
        проходов = int(getattr(self.settings, "assistant_digest_passes",
                               DIGEST_PASSES) or 0)
        # Ноль проходов отсекает нарезка ниже («части[:проходов]»), и второй
        # проверки здесь нет намеренно: она прикрывала бы первую, и ошибка в
        # правиле «сколько проходов делать» осталась бы невидимой.
        if not dropped:
            return "", []
        llm = self.reports.get_llm()
        if llm is None:
            return "", []

        порция = max(1, int(getattr(self.settings, "assistant_digest_chunk",
                                    DIGEST_CHUNK) or DIGEST_CHUNK))
        части = [list(dropped[начало:начало + порция])
                 for начало in range(0, len(dropped), порция)][:проходов]
        слов = int(getattr(self.settings, "assistant_digest_words",
                           DIGEST_WORDS) or DIGEST_WORDS)
        выписки: List[str] = []
        вошли: List[Dict[str, Any]] = []
        for номер, часть in enumerate(части, start=1):
            подсказка = DIGEST_PROMPT.format(
                question=question, number=номер, total=len(части),
                sources=_render_sources(часть), limit=слов)
            try:
                ответ = llm.complete(DIGEST_SYSTEM_PROMPT, подсказка,
                                     max_tokens=int(слов * 2), temperature=0.0)
            except Exception:          # noqa: BLE001 — проход не обязателен
                # Один непрошедший проход не должен ронять весь ответ: то, что
                # уже выписано, полезно и без него.
                trail.append(f"выписка {номер} из {len(части)}: модель не ответила")
                continue
            ответ = (ответ or "").strip()
            метки = ", ".join(item["label"] for item in часть)
            trail.append(f"выписка из фрагментов {метки} "
                         f"({номер} из {len(части)})")
            if not ответ or _пусто_по_вопросу(ответ):
                continue
            кусок = f"— часть {номер} (фрагменты {метки}):\n{ответ}"
            # Из отведённого места не выходим: иначе выписки вытолкнут сами
            # источники. Модель отвечает длиннее, чем её просили, постоянно.
            if room > 0 and sum(len(item) for item in выписки) + len(кусок) > room:
                trail.append(f"выписка {номер}: не поместилась в отведённое место")
                break
            выписки.append(кусок)
            вошли.extend(часть)

        if not выписки:
            return "", []
        return ("\n### ВЫПИСКИ ИЗ ОСТАЛЬНОГО МАТЕРИАЛА\n"
                "Найденного по вопросу больше, чем помещается в окно за один "
                "раз. Ниже — выписки из фрагментов, тексты которых в этот "
                "промпт не вошли. Они такой же материал, как ИСТОЧНИКИ выше: "
                "ссылайся на них теми же метками и учитывай в ответе наравне.\n"
                + "\n\n".join(выписки) + "\n"), вошли

    def _context_chars(self) -> int:
        """Сколько ЗНАКОВ можно отдать промпту. Выводится из окна модели.

        Раньше это число стояло в настройках руками — 52 000 — и с окном
        модели связано не было. Замер показал расхождение в полтора раза:

            окно 32768 − ответ 4000 − запас 512 = 28 256 токенов
            28 256 × 1,5 знака = 42 384 знака
            минус системная инструкция (4315) = 38 069 знаков

        Тридцать восемь тысяч против пятидесяти двух в настройке. Отсюда и
        «36061 токенов, а размер 32768»: бюджет соблюдался, промпт не влезал.

        Настройка ``assistant_context_chars`` осталась, но теперь она может
        только УМЕНЬШИТЬ вывод, не увеличить: маленькое значение ставят
        осознанно, а большое — от незнания, и раньше оно роняло ответ.
        """
        окно = int(getattr(self.settings, "llm_context_tokens", 0) or 32768)
        ответ = self._max_tokens()
        знаков_на_токен = max(0.5, float(
            getattr(self.settings, "assistant_chars_per_token", 0) or CHARS_PER_TOKEN))
        выведено = int((окно - ответ - TOKEN_SAFETY) * знаков_на_токен
                       - len(ASSISTANT_SYSTEM_PROMPT) - len(ASSISTANT_PROMPT))
        # Пол ставим ТОЛЬКО выведенному: окно меньше пола — это опечатка в
        # настройке модели, а не решение. Заданное вручную маленькое значение
        # пола не знает: его ставят, когда окно и правда крошечное, и
        # подменять такое решение своим нельзя.
        выведено = max(MIN_LIBRARY_CHARS, выведено)
        задано = int(getattr(self.settings, "assistant_context_chars", 0) or 0)
        return min(выведено, задано) if задано > 0 else выведено

    def _fit_tokens(self, sources: List[Dict[str, Any]],
                    documents: List[Dict[str, Any]],
                    собрать: "Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], str]",
                    *, history: Sequence[Dict[str, str]],
                    answer_tokens: int, extra: int = 0
                    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], str,
                               List[Dict[str, Any]]]:
        """Урезать материал, пока СОБРАННЫЙ промпт не влезет в окно модели.

        Отдел получил «36061 токенов, а размер 32768»: модель не ответила
        вовсе. Бюджет при этом соблюдался — но соблюдался по частям, а в окно
        уходит целое. Не считались системная инструкция (около 1800 токенов),
        шапки шаблона, блок «документы, названные в вопросе» и место под сам
        ответ. Вдобавок оценка «2,4 знака на токен» взята от английского
        текста: русское техническое слово разбивается мельче, и на живом
        материале вышло около полутора.

        Считать точно нечем: токенизатор лежит в модели, спрашивать её на
        каждый вопрос — лишний круг по сети. Поэтому оценка с запасом ВНИЗ
        (``assistant_chars_per_token``) и проверка по собранной строке.
        Ошибаться можно только в одну сторону: заниженная оценка отнимает у
        ответа немного материала, завышенная роняет ответ целиком.

        Режем с конца выдачи — там уже хвост относимости. Возвращаем не
        только оставшееся, но и СНЯТОЕ: выбрасывать его нельзя, его прочтут
        отдельными проходами (см. ``_digest``).

        ``extra`` — сколько знаков придержать под то, что добавится в промпт
        потом (выписки). Держать место заранее обязательно: добавлять их по
        остаточному принципу значит снова переполнить окно.
        """
        окно = int(getattr(self.settings, "llm_context_tokens", 0) or 32768)
        знаков_на_токен = float(
            getattr(self.settings, "assistant_chars_per_token", 0) or CHARS_PER_TOKEN)
        знаков_на_токен = max(0.5, знаков_на_токен)
        место = окно - int(answer_tokens or 0) - TOKEN_SAFETY
        постоянное = (len(ASSISTANT_SYSTEM_PROMPT) + max(0, int(extra))
                      + sum(len(item.get("content") or "") for item in history))

        оставшиеся = list(sources)
        карточки = list(documents)
        снятые: List[Dict[str, Any]] = []
        prompt = собрать(оставшиеся, карточки)
        while len(оставшиеся) > 1 and место > 0:
            токенов = int((len(prompt) + постоянное) / знаков_на_токен)
            if токенов <= место:
                break
            # Последний фрагмент не снимаем никогда: без единого источника
            # отвечать не на что, а «отвечу по памяти» — худший исход из всех.
            снятые.insert(0, оставшиеся.pop())
            карточки = self._document_cards(оставшиеся)
            prompt = собрать(оставшиеся, карточки)
        return оставшиеся, карточки, prompt, снятые

    def _too_big(self, prompt: str, history: Sequence[Dict[str, str]],
                 answer_tokens: int) -> bool:
        """Не влезает ли собранный промпт в окно модели вместе с ответом."""
        окно = int(getattr(self.settings, "llm_context_tokens", 0) or 32768)
        знаков_на_токен = max(0.5, float(
            getattr(self.settings, "assistant_chars_per_token", 0) or CHARS_PER_TOKEN))
        знаков = (len(prompt) + len(ASSISTANT_SYSTEM_PROMPT)
                  + sum(len(item.get("content") or "") for item in history))
        return int(знаков / знаков_на_токен) > окно - int(answer_tokens or 0) - TOKEN_SAFETY

    def _digest_room(self) -> int:
        """Сколько знаков придержать под выписки, если они понадобятся."""
        проходов = int(getattr(self.settings, "assistant_digest_passes",
                               DIGEST_PASSES) or 0)
        if проходов <= 0:
            return 0
        слов = int(getattr(self.settings, "assistant_digest_words",
                           DIGEST_WORDS) or DIGEST_WORDS)
        # Русское слово с пробелом — около семи знаков; плюс шапка блока.
        return проходов * слов * 7 + 600

    def _digest_note(self, dropped: Sequence[Dict[str, Any]],
                     digest: str, ещё: Sequence[Dict[str, Any]]) -> str:
        """Что сказать инженеру про материал, не влезший в окно.

        Молчать нельзя ни в одном из случаев: ответ собран не так, как
        обычно, и человек должен это знать, читая его.
        """
        всего = len(dropped) + len(ещё)
        if digest and not ещё:
            return (f"материал не помещался в окно модели целиком: "
                    f"{len(dropped)} фрагментов прочитаны отдельными "
                    f"проходами и вошли в ответ выписками")
        if digest and ещё:
            return (f"материал не помещался в окно модели целиком: "
                    f"{len(dropped)} фрагментов прочитаны выписками, ещё "
                    f"{len(ещё)} не поместились и в ответ не вошли")
        return (f"материал не поместился в окно модели: {всего} фрагментов "
                f"в ответ не вошли. Разбор частями выключен настройкой "
                f"assistant_digest_passes — включите его или спросите у́же "
                f"по теме")

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
        window = self._context_chars()
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

    def _units(self, question: str) -> List[str]:
        """Узлы названного в вопросе целого — по справочнику состава.

        Справочник необязателен: нет файла, испорчен, целое незнакомо —
        разбор идёт как прежде. Ронять вопрос инженера из-за справочника
        нельзя, поэтому ошибки здесь глотаются молча; что справочник отбросил
        и почему, показывает команда «reportgen parts».
        """
        # Ноль и меньше — «не разбирать вовсе», и решает это units_for. Второй
        # проверки здесь не ставим: она бы прикрывала первую, и ошибка в
        # правиле «сколько узлов брать» осталась бы невидимой для проверок.
        предел = int(getattr(self.settings, "assistant_parts", parts.MAX_UNITS))
        try:
            return parts.units_for(question,
                                   getattr(self.settings, "parts_path", None),
                                   limit=предел)
        except Exception:              # noqa: BLE001 — разбор не обязателен
            return []

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
        узлы = self._units(question) if rounds else []
        if not rounds or (not first and not узлы):
            # Разбор выключен или библиотека ничего не дала: заходы по пустому
            # месту только сожгут время модели.
            return list(first), []

        rankings: List[List[Hit]] = [list(first)] if first else []
        # Вес каждого списка при слиянии. Список по САМОМУ вопросу весит
        # полную единицу, списки по узлам состава — меньше: см. _merge.
        веса: List[float] = [1.0] * len(rankings)
        pinned: List[Hit] = []
        seen = {hit.chunk.chunk_id for hit in first}
        trail: List[Step] = []
        catalog = self._catalog_block(chat, first)
        case_block = self._case_block(chat)

        # Разбор состава идёт ПЕРВЫМ и без обращения к модели. Вопрос называет
        # целое («тракт приёма РРЛС»), а в библиотеке лежат паспорта его
        # узлов — «АДЭ-5», «МШУ-2», «ДМА 256», и слов «тракт приёма» в них
        # нет. Поиск ищет похожее на запрос; здесь нужно другое — то, из чего
        # запрошенное состоит. Замер и обоснование — в reportgen.parts.
        for узел in узлы:
            step = Step("искать", узел)
            trail.append(step)
            if on_step is not None:
                on_step(step)
            найдено = self._step_search(step, chat, rerank=False)
            свежие = [hit for hit in найдено if hit.chunk.chunk_id not in seen]
            seen.update(hit.chunk.chunk_id for hit in свежие)
            if свежие:
                rankings.append(свежие)
                веса.append(PART_WEIGHT)
            step.note = f"узел из состава; {found_note(len(свежие))}"

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
                веса.append(1.0)
            # У «читать» заметка своя — что именно открыли; счёт новых
            # фрагментов дописываем к ней, а не вместо неё.
            counted = found_note(len(fresh))
            step.note = f"{note}; {counted}" if note else counted

        return self._merge(rankings, pinned, top_k, веса), trail

    def _merge(self, rankings: Sequence[Sequence[Hit]], pinned: Sequence[Hit],
               top_k: int | None,
               weights: Sequence[float] | None = None) -> List[Hit]:
        """Сводит находки всех заходов в один список.

        Слияние по обратным рангам (RRF): шкалы BM25, косинуса и реранка
        между собой не сравнимы, а места в списках — сравнимы. Фрагмент,
        который всплыл в двух заходах по разным словам, поднимается выше —
        и это именно то, что нужно: два независимых способа его найти.

        Списки при этом НЕ равноправны, и в этом была ошибка. Поиск по
        самому вопросу весит единицу, поиск по одному узлу состава —
        ``PART_WEIGHT``. С равными весами восемь узловых списков вытесняли
        то, что нашлось по вопросу: отдел это и увидел — «стало медленнее, а
        качество не улучшилось, где-то даже ухудшилось».

        Веса и списки сводим парами, а не по номеру: пустые списки
        отбрасываются, и нумерация без пары разъехалась бы молча.
        """
        wanted = int(top_k or getattr(self.settings, "assistant_top_k", 0)
                     or self.settings.retrieval_top_k)
        веса = list(weights or ())
        пары = [(list(item), веса[номер] if номер < len(веса) else 1.0)
                for номер, item in enumerate(rankings) if item]
        lists = [список for список, _вес in пары]
        if not lists:
            merged: List[Hit] = []
        elif len(lists) == 1:
            merged = lists[0][:wanted]
        else:
            merged = reciprocal_rank_fusion(
                lists, top_k=wanted, weights=[вес for _список, вес in пары])
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

    def _step_search(self, step: Step, chat: Chat, *,
                     rerank: bool = True) -> List[Hit]:
        """Поиск одним заходом разбора.

        ``rerank=False`` — для разбора состава: там запрос из одного-двух
        слов («антенна», «демодулятор»), уточнять реранку нечего, а поисков
        подряд до восьми. Реранк на каждом — восемь обращений к модели вместо
        одного, и это ровно та задержка, которую отдел заметил.
        """
        retriever = self.reports.get_retriever()
        if retriever is None:
            return []
        domains = [chat.domain] if chat.domain else None
        # Тот же отбор источников, что и у первого поиска: иначе заход разбора
        # тихо приносил бы то, что человек фильтром отключил.
        doc_types = SOURCE_DOC_TYPES.get(chat_sources(chat))
        try:
            try:
                найдено = retriever.search(step.argument, top_k=RESEARCH_TOP_K,
                                           domains=domains, doc_types=doc_types,
                                           rerank=rerank)
            except TypeError:
                # Поисковик постарше: без направлений либо без ключа rerank.
                # Разбор не обязан падать из-за необязательного удобства.
                try:
                    найдено = retriever.search(step.argument,
                                               top_k=RESEARCH_TOP_K,
                                               domains=domains,
                                               doc_types=doc_types)
                except TypeError:
                    найдено = retriever.search(step.argument, top_k=RESEARCH_TOP_K)
        except Exception:              # noqa: BLE001 — заход не обязателен
            return []
        return _only_types(найдено, doc_types)

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

        Второй заход по той же беде. Отдел: «в примечаниях пишет, что нет
        документа ITU-T G.733, хотя вот он в библиотеке». Документ лежал на
        полке, пять фрагментов, а спросили о нём так: «в чем разница
        стандарта g 733 от 734». Ни одно из двух обозначений не опознавалось:
        серию от номера отделял пробел, а у второго номера серии не было
        вовсе. Сверять было нечего, и модель написала, чего ей не хватает.

        Догадки (:func:`implied_designations`) сверяются здесь же, но по
        другому правилу: в подсказку идёт только то, что в описи НАШЛОСЬ.
        Про догадку, которой в библиотеке нет, инженеру не докладывают —
        он про неё не спрашивал, а «X.30 НЕ ЧИСЛИТСЯ» в ответ на вопрос про
        тридцать каналов было бы ровно тем враньём, от которого мы уходим.
        """
        предыдущее = [item["content"] for item in history if item["role"] == "user"][-1:]
        текст = " ".join([question, *предыдущее])
        обозначения = designations_in(текст)
        догадки = implied_designations(текст)
        if not обозначения and not догадки:
            return "", []
        строки: List[str] = []
        найденные: List[str] = []

        def свериться(имя: str, догадка: bool) -> None:
            doc_id = self._resolve_document(имя)
            документ = self.repos.documents.by_doc_id(doc_id) if doc_id else None
            if документ is None:
                if not догадка:
                    строки.append(f"- {имя} — в библиотеке НЕ ЧИСЛИТСЯ")
                return
            if doc_id in найденные:
                return
            найденные.append(doc_id)
            пометка = "" if документ.status == "current" else f", {документ.status}"
            строки.append(
                f"- {имя} — ЧИСЛИТСЯ: «{документ.title or doc_id}» "
                f"({doc_id}, фрагментов {документ.chunk_count}{пометка})"
            )

        for имя in обозначения:
            свериться(имя, False)
        for имя in догадки:
            свериться(имя, True)
        if not строки:
            return "", []
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
        # Подходящих бывает несколько: у одной рекомендации в библиотеке
        # лежат сама она, поправка к ней и исправление. Берём ЛУЧШЕГО, а не
        # первого попавшегося — см. _вес_совпадения.
        лучший = ""
        лучший_вес: tuple | None = None
        for doc_id, заголовок in self._document_candidates(искомое):
            if not doc_id:
                continue
            имя_файла = name_parts(doc_id.rsplit("/", 1)[-1])
            название = name_parts(заголовок)
            # Точное совпадение — с именем файла или с названием целиком.
            if искомое in (имя_файла, название, name_parts(doc_id)):
                return doc_id
            # Обозначение стоит в начале названия: «ГОСТ Р 53363» находит
            # «ГОСТ Р 53363-2009. Цифровые радиорелейные линии».
            if название[:len(искомое)] == искомое or имя_файла[:len(искомое)] == искомое:
                способ = 2
            elif _подряд(название, искомое) or _подряд(имя_файла, искомое):
                # Последнее средство: назвали кусок из середины названия.
                способ = 1
            else:
                continue
            вес = _вес_совпадения(способ, заголовок)
            if лучший_вес is None or вес > лучший_вес:
                лучший, лучший_вес = doc_id, вес
        return лучший

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
    "reports": "ОТЧЁТ ОТДЕЛА по конкретному случаю",
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
        # Вид документа стоит рядом с фрагментами, а не только в карте
        # найденного. Иначе, дойдя до текста, модель уже не помнит, чем был
        # источник, и разбор одного канала цитируется как определение.
        вид = _DOC_TYPE_TITLES.get(items[0].get("doc_type", ""), "")
        заголовок = f"— — — ДОКУМЕНТ: {title}"
        if вид:
            заголовок += f" [{вид}]"
        blocks.append(заголовок + " — — —")
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
