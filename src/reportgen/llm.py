"""Клиент к локальной языковой модели.

Все распространённые локальные движки (llama.cpp server, Ollama, vLLM,
SGLang, TGI) отдают OpenAI-совместимый ``/v1/chat/completions``, поэтому код
конвейера не знает, что именно у него под капотом: меняется только
``--base-url``. Никаких внешних зависимостей — обычный ``urllib``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import _http
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Protocol


def _timed_out(error: BaseException) -> bool:
    """Ответа не дождались (а не «не смогли подключиться»).

    Различать это важно: связи нет — повторить попытку разумно, сервер мог
    только подниматься; ответа нет — повторять бессмысленно и вредно, модель
    занята, и второй такой же запрос удлиняет очередь отдела.
    """
    if isinstance(error, TimeoutError):
        return True
    reason = getattr(error, "reason", None)
    return isinstance(reason, TimeoutError)


class LLMError(RuntimeError):
    """Ошибка обращения к модели."""


#: Теги, которыми рассуждающие модели обрамляют черновую мысль. Qwen3 —
#: гибридная: размышление у неё включено ПО УМОЛЧАНИЮ, и блок идёт первым,
#: до ответа.
ОТКРЫТЬ_МЫСЛЬ = "<think>"
ЗАКРЫТЬ_МЫСЛЬ = "</think>"


class ОтсекательМысли:
    """Убирает блок ``<think>…</think>`` из ответа, в том числе из потока.

    Зачем это здесь, а не в помощнике. Рабочая модель отдела — Qwen3-14B, и
    она рассуждает, не спрашивая. Куда попадёт рассуждение, зависит от сборки
    llama.cpp: старая кладёт его прямо в ``content``, новая выносит в
    ``reasoning_content``. Полагаться на сборку нельзя — на изолированной
    машине её не обновить, — поэтому отсекаем сами и при любой.

    Чего это стоило отделу. Токены рассуждения считаются в тот же потолок,
    что и ответ. У коротких служебных заходов потолок маленький: сто двадцать
    токенов у планировщика разбора, двести у добора по названию и у сверки
    комплектности. Рассуждение съедало их ЦЕЛИКОМ, и до строки с ответом дело
    не доходило. В логе отдела это видно по трём задачам подряд: генерация
    встала ровно на 120, ровно на 200 и ровно на 200 токенов — точно в свои
    потолки из кода. То есть добор документов и цикл заходов по библиотеке на
    живой машине не работали вовсе, и молча.

    Разрыв тега между кусками потока — обычное дело: «<thi» приходит одним
    куском, «nk>» следующим. Поэтому наружу отдаётся не всё, что накопилось, а
    всё за вычетом хвоста, который ещё может оказаться началом тега.
    """

    def __init__(self) -> None:
        self._буфер = ""
        self._внутри = False
        #: Поток кончился, а мысль не закрылась: генерация уперлась в потолок
        #: прямо посреди рассуждения. Ответа нет вовсе — и это надо знать.
        self.оборвалось_на_мысли = False

    def кусок(self, текст: str) -> str:
        """Очередной кусок потока — вернуть то, что можно показать сейчас."""
        self._буфер += текст
        наружу: List[str] = []
        while True:
            if self._внутри:
                край = self._буфер.find(ЗАКРЫТЬ_МЫСЛЬ)
                if край == -1:
                    # Держим хвост, который может быть началом закрывающего
                    # тега; остальное — мысль, её не показываем.
                    self._буфер = self._буфер[-(len(ЗАКРЫТЬ_МЫСЛЬ) - 1):]
                    break
                self._буфер = self._буфер[край + len(ЗАКРЫТЬ_МЫСЛЬ):]
                self._внутри = False
                continue
            край = self._буфер.find(ОТКРЫТЬ_МЫСЛЬ)
            if край == -1:
                # То же с открывающим: «<thi» в конце куска — ещё не текст.
                держим = len(ОТКРЫТЬ_МЫСЛЬ) - 1
                наружу.append(self._буфер[:-держим] if держим else self._буфер)
                self._буфер = self._буфер[-держим:] if держим else ""
                break
            наружу.append(self._буфер[:край])
            self._буфер = self._буфер[край + len(ОТКРЫТЬ_МЫСЛЬ):]
            self._внутри = True
        return "".join(наружу)

    def хвост(self) -> str:
        """Поток кончился — отдать остаток.

        Если мысль не закрылась, остаток — это её обрывок, и показывать его
        нельзя: инженер увидит черновые рассуждения вместо ответа. Зато
        отметка ``оборвалось_на_мысли`` скажет наверху, почему ответ пуст.
        """
        if self._внутри:
            self.оборвалось_на_мысли = True
            self._буфер = ""
            return ""
        остаток, self._буфер = self._буфер, ""
        return остаток


def без_мысли(текст: str) -> str:
    """Тот же отсекатель для целого ответа, пришедшего одним куском."""
    отсекатель = ОтсекательМысли()
    return (отсекатель.кусок(текст) + отсекатель.хвост()).strip()


class LLM(Protocol):
    name: str

    @staticmethod
    def _timed_out(error: BaseException) -> bool:
        return _timed_out(error)

    def complete(self, system: str, user: str, *, max_tokens: int = 1200,
                 temperature: float = 0.2) -> str:
        ...

    def stream(self, system: str, user: str, *, max_tokens: int = 1200,
               temperature: float = 0.2, history: List[Dict[str, str]] | None = None
               ) -> Iterator[str]:
        """Потоковая выдача по кускам. Нужна помощнику: ответ на 500 слов
        появляется сразу, а не через полминуты молчания."""
        ...


@dataclass
class OpenAICompatLLM:
    """Клиент к любому OpenAI-совместимому локальному серверу."""

    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = "local-model"
    api_key: str = "not-needed"
    timeout: float = 600.0
    retries: int = 3
    seed: int | None = 0

    #: Чем кончилась последняя генерация: «stop» — сама, «length» — упёрлась
    #: в потолок токенов, «размышление» — потолок кончился, пока модель ещё
    #: рассуждала. Пусто — ответа ещё не было. Обрыв по длине прежде не
    #: замечался нигде: оборванный на середине таблицы ответ ложился в
    #: переписку как законченный.
    последний_обрыв: str = ""
    #: Расход токенов последнего ответа со слов самого сервера
    #: (``usage``). Нужен, чтобы знать НАСТОЯЩИЙ размер промпта, а не
    #: оценку по знакам.
    последний_расход: Dict[str, Any] = field(default_factory=dict)
    #: Видели ли в потоке отдельное поле рассуждения. Признак того, что
    #: сборка сервера выносит мысль сама, и отсекать нечего.
    размышляет: bool = False

    @property
    def name(self) -> str:
        return f"{self.model} @ {self.base_url}"

    def available(self, timeout: float = 2.0) -> bool:
        """Отвечает ли сервер модели прямо сейчас.

        Проверка нужна интерфейсу: инженер должен видеть, что llama-server
        не поднят, до того как задаст вопрос и прождёт минуту впустую.
        Спрашиваем список моделей — самый дешёвый запрос, без генерации.
        Ждём недолго и без повторов: это индикатор, а не работа.
        """
        request = urllib.request.Request(
            url=f"{self.base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
            method="GET",
        )
        try:
            with _http.urlopen(request, timeout=timeout) as response:
                return 200 <= response.status < 300
        except Exception:          # noqa: BLE001 — любой сбой значит «недоступен»
            return False

    def context_tokens(self, timeout: float = 3.0) -> int:
        """Настоящее окно контекста сервера — то самое «-c» при запуске.

        Зачем спрашивать, а не брать из настройки. Окно стоит в двух местах:
        в командной строке llama-server и в settings.json. Держать их
        согласованными руками не получилось — отдел получил «36061 токенов, а
        размер 32768»: промпт собирался под одно число, а сервер работал по
        другому. И наоборот: подняли «-c» ради развёрнутых ответов, а
        помощник об этом не узнал и продолжил урезать материал под прежнее.

        llama-server отдаёт это число сам, по ``GET /props``: там лежит
        ``default_generation_settings.n_ctx`` — размер окна ОДНОГО слота. Он
        уже поделён на число параллельных слотов («--parallel»), то есть это
        ровно то, что достанется нашему запросу, а не общий буфер.

        Ноль — «сервер не сказал»: не llama.cpp, старая сборка, сервер не
        поднят. Тогда работает настройка, как работала раньше.
        """
        адрес = self.base_url.rstrip("/")
        # /props лежит В КОРНЕ сервера, а не внутри /v1: базовый адрес в
        # настройке указывает на /v1, и его надо отрезать.
        if адрес.endswith("/v1"):
            адрес = адрес[: -len("/v1")]
        request = urllib.request.Request(
            url=f"{адрес}/props",
            headers={"Authorization": f"Bearer {self.api_key}"},
            method="GET",
        )
        try:
            with _http.urlopen(request, timeout=timeout) as response:
                данные = json.loads(response.read().decode("utf-8"))
        except Exception:          # noqa: BLE001 — не сказал, и ладно
            return 0
        настройки = данные.get("default_generation_settings") or {}
        for место in (настройки.get("n_ctx"), данные.get("n_ctx")):
            try:
                число = int(место)
            except (TypeError, ValueError):
                continue
            if число > 0:
                return число
        return 0

    def _payload(self, system: str, user: str, max_tokens: int, temperature: float,
                 history: List[Dict[str, str]] | None = None,
                 stream: bool = False) -> Dict[str, Any]:
        messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
        for item in history or []:
            if item.get("role") in ("user", "assistant") and item.get("content"):
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": user})
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            # Просьба не рассуждать. Работает не везде: llama.cpp читает это
            # поле только при «--jinja», а в поставке отдела его нет. Вреда
            # от лишнего поля никакого — сервер, который его не знает, просто
            # проходит мимо, — а там, где оно сработает, потолок целиком
            # достаётся ответу. Настоящая защита — не здесь, а в
            # ОтсекательМысли и в «/no_think» служебных подсказок: они
            # работают при любой сборке.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if stream:
            payload["stream"] = True
            # Просим прислать расход токенов последним куском. Без этого
            # настоящий размер промпта неизвестен: оценка «полтора знака на
            # токен» ничем не проверялась.
            payload["stream_options"] = {"include_usage": True}
        if self.seed is not None:
            # Воспроизводимость отчёта — инвариант из док. 01, раздел 1.4.
            payload["seed"] = self.seed
        return payload

    def _request(self, payload: Dict[str, Any]) -> urllib.request.Request:
        return urllib.request.Request(
            url=f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

    def complete(self, system: str, user: str, *, max_tokens: int = 1200,
                 temperature: float = 0.2,
                 history: List[Dict[str, str]] | None = None) -> str:
        request = self._request(
            self._payload(system, user, max_tokens, temperature, history)
        )

        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with _http.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                выбор = body["choices"][0]
                # Обрыв по потолку токенов запоминаем: у служебных заходов
                # потолок маленький, и молчаливый обрыв там выглядел как
                # «модель ничего не нашла».
                self.последний_обрыв = str(выбор.get("finish_reason") or "")
                self.последний_расход = dict(body.get("usage") or {})
                return без_мысли(выбор["message"]["content"])
            except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as error:
                last_error = error
                # Не дождались ответа — повторять НЕЛЬЗЯ. Молчание модели
                # означает, что она занята очередью отдела, и второй такой же
                # запрос очередь только удлиняет. А человек за это время не
                # видит ничего: при трёх попытках по пятнадцать минут беда
                # доходила до него через сорок пять минут молчания.
                if _timed_out(error):
                    break
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
        raise LLMError(f"обращение к модели не удалось: {last_error}") from last_error

    def stream(self, system: str, user: str, *, max_tokens: int = 1200,
               temperature: float = 0.2,
               history: List[Dict[str, str]] | None = None) -> Iterator[str]:
        """Читает поток server-sent events и отдаёт куски текста по мере готовности."""
        request = self._request(
            self._payload(system, user, max_tokens, temperature, history, stream=True)
        )
        отсекатель = ОтсекательМысли()
        self.последний_обрыв = ""
        self.последний_расход = {}
        try:
            with _http.urlopen(request, timeout=self.timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    # Расход токенов приходит ОТДЕЛЬНЫМ куском, и в нём
                    # choices пуст. Прежде такой кусок отбрасывался вместе со
                    # всем остальным, и число токенов промпта пропадало.
                    if chunk.get("usage"):
                        self.последний_расход = dict(chunk["usage"])
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    if choices[0].get("finish_reason"):
                        self.последний_обрыв = str(choices[0]["finish_reason"])
                    delta = choices[0].get("delta") or {}
                    # Новые сборки llama.cpp выносят рассуждение в отдельное
                    # поле. Наружу оно не идёт, но говорит, что модель занята
                    # делом, — иначе инженер полминуты смотрит на пустой экран.
                    if delta.get("reasoning_content"):
                        self.размышляет = True
                    piece = delta.get("content")
                    if piece:
                        видимое = отсекатель.кусок(piece)
                        if видимое:
                            yield видимое
            хвост = отсекатель.хвост()
            if хвост:
                yield хвост
            if отсекатель.оборвалось_на_мысли:
                self.последний_обрыв = "размышление"
        except (urllib.error.URLError, TimeoutError) as error:
            raise LLMError(f"обращение к модели не удалось: {error}") from error


@dataclass
class StubLLM:
    """Офлайн-заглушка: собирает текст секции из переданных ей фактов.

    Нужна, чтобы конвейер, шаблоны и верификатор можно было отлаживать и
    тестировать без GPU и без модели. Заглушка намеренно не выдумывает
    ничего сверх фактов — так проверяется, что «зелёный» результат
    верификатора достижим.
    """

    name: str = "stub"

    def available(self, timeout: float = 2.0) -> bool:
        return True

    def stream(self, system: str, user: str, *, max_tokens: int = 1200,
               temperature: float = 0.2,
               history: List[Dict[str, str]] | None = None) -> Iterator[str]:
        text = self.complete(system, user, max_tokens=max_tokens, temperature=temperature,
                             history=history)
        for word in text.split(" "):
            yield word + " "

    def complete(self, system: str, user: str, *, max_tokens: int = 1200,
                 temperature: float = 0.2,
                 history: List[Dict[str, str]] | None = None) -> str:
        # Помощник спрашивает, что делать следующим шагом разбора. Заглушка
        # ничего не ищет: она отвечает по тому, что ей дали, и лишний заход
        # только сжёг бы время. Отвечаем «хватит» — это честный шаг, а не
        # молчание, которое разбор трактовал бы как непонятый ответ.
        if "СЛЕДУЮЩИЙ ШАГ" in user:
            return "ХВАТИТ"
        question = _extract_block(user, "ВОПРОС")
        if question:
            return _stub_answer(question, _extract_block(user, "ИСТОЧНИКИ"))
        section = _extract_block(user, "СЕКЦИЯ")
        facts = _extract_block(user, "ФАКТЫ")
        sources = _extract_block(user, "ИСТОЧНИКИ")

        lines: List[str] = []
        title = section.splitlines()[0].strip() if section else "Раздел"
        lines.append(f"Ниже приведены данные по разделу «{title}».")
        table = [line for line in facts.splitlines() if line.startswith("|")]
        if table:
            lines.append("")
            lines.extend(table)
        bullets = [line for line in facts.splitlines() if line.startswith("- ")]
        if bullets:
            lines.append("")
            lines.extend(bullets)
        for line in facts.splitlines():
            if line.startswith("ОТСУТСТВУЮТ ОБЯЗАТЕЛЬНЫЕ ДАННЫЕ:"):
                gap = line.split(":", 1)[1].split(".")[0].strip()
                lines.append("")
                lines.append(f"[ТРЕБУЕТ ПРОВЕРКИ: не переданы измерения — {gap}]")

        citations = [line.split("]")[0] + "]" for line in sources.splitlines() if line.startswith("[S")]
        if citations:
            lines.append("")
            lines.append("Использованные источники: " + ", ".join(citations) + ".")
        if not table and not bullets:
            lines.append("[ТРЕБУЕТ ПРОВЕРКИ: для раздела не передано ни одного факта]")
        return "\n".join(lines)


def _stub_answer(question: str, sources: str) -> str:
    """Ответ помощника для офлайн-режима: только по переданным источникам."""
    citations = [line.split("]")[0] + "]" for line in sources.splitlines() if line.startswith("[S")]
    lines = [f"По вопросу «{question.strip()[:200]}» в библиотеке найдено следующее."]
    # Берём осмысленный кусок первого источника, включая таблицы: в офлайн-режиме
    # это единственный способ увидеть, как выглядит настоящий ответ.
    quoted: list[str] = []
    for line in sources.splitlines():
        if line.startswith("[S") and quoted:
            break
        if line.startswith("[S"):
            continue
        quoted.append(line)
        if len(quoted) >= 14:
            break
    if quoted:
        lines.append("")
        lines.extend(quoted)
    if citations:
        lines.append("")
        lines.append("Источники: " + ", ".join(citations) + ".")
    else:
        lines.append("")
        lines.append("В библиотеке ничего подходящего не нашлось — уточните запрос "
                     "или загрузите нужный документ.")
    return "\n".join(lines)


def _extract_block(text: str, name: str) -> str:
    """Достаёт содержимое блока '### ИМЯ' из промпта."""
    marker = f"### {name}"
    start = text.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    end = text.find("\n### ", start)
    return text[start:end if end != -1 else len(text)].strip()


def build_llm(kind: str, **kwargs: Any) -> LLM:
    if kind == "stub":
        return StubLLM()
    if kind in {"openai", "local", "llamacpp", "vllm", "ollama"}:
        return OpenAICompatLLM(**kwargs)
    raise ValueError(f"неизвестный тип клиента модели: {kind}")
