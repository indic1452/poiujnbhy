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
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Protocol


class LLMError(RuntimeError):
    """Ошибка обращения к модели."""


class LLM(Protocol):
    name: str

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

    @property
    def name(self) -> str:
        return f"{self.model} @ {self.base_url}"

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
        }
        if stream:
            payload["stream"] = True
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
                return body["choices"][0]["message"]["content"].strip()
            except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as error:
                last_error = error
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
        try:
            with _http.urlopen(request, timeout=self.timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        yield piece
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
