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
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol


class LLMError(RuntimeError):
    """Ошибка обращения к модели."""


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str, *, max_tokens: int = 1200,
                 temperature: float = 0.2) -> str:
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

    def complete(self, system: str, user: str, *, max_tokens: int = 1200,
                 temperature: float = 0.2) -> str:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if self.seed is not None:
            # Воспроизводимость отчёта — инвариант из док. 01, раздел 1.4.
            payload["seed"] = self.seed

        request = urllib.request.Request(
            url=f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                return body["choices"][0]["message"]["content"].strip()
            except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as error:
                last_error = error
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
        raise LLMError(f"обращение к модели не удалось: {last_error}") from last_error


@dataclass
class StubLLM:
    """Офлайн-заглушка: собирает текст секции из переданных ей фактов.

    Нужна, чтобы конвейер, шаблоны и верификатор можно было отлаживать и
    тестировать без GPU и без модели. Заглушка намеренно не выдумывает
    ничего сверх фактов — так проверяется, что «зелёный» результат
    верификатора достижим.
    """

    name: str = "stub"

    def complete(self, system: str, user: str, *, max_tokens: int = 1200,
                 temperature: float = 0.2) -> str:
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
