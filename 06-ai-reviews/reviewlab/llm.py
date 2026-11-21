"""LLM-бэкенд через OpenAI-совместимый HTTP API на stdlib.

`requests` на целевой машине не работает (локальный прокси его блокирует),
поэтому здесь только `urllib.request`. Никаких SDK, никакой сети при сборке:
сеть нужна лишь когда бэкенд реально вызывают и есть ключ.

Если `OPENAI_API_KEY` не задан — бэкенд честно сообщает, что недоступен, и
`build.py` продолжает сборку без него. Это заглушка с рабочим интерфейсом, а
не имитация результата: никакие LLM-метрики не выдумываются.

Переменные окружения:
    OPENAI_API_KEY   — обязательный ключ;
    OPENAI_BASE_URL  — по умолчанию https://api.openai.com/v1;
    OPENAI_MODEL     — по умолчанию gpt-4o-mini;
    OPENAI_TIMEOUT   — таймаут одного запроса, секунды (по умолчанию 30).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from .dataset import LABELS
from .text import normalize_text, truncate

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = (
    "Ты — разметчик отзывов на маркетплейсах. Определи одну основную жалобу "
    "из списка или отметь, что жалобы нет. Отвечай строго JSON без пояснений."
)

USER_PROMPT = """Классы:
{classes}

Отзыв: «{text}»

Верни JSON: {{"label": "<один из классов>"}}"""


class LLMUnavailable(RuntimeError):
    """Бэкенд нельзя использовать: нет ключа, отключён или ответ не получен."""


@dataclass
class LLMResponse:
    """Ответ модели, уже разобранный."""

    label: str
    raw: str
    latency_ms: float
    ok: bool = True
    error: str = ""
    from_cache: bool = False


def parse_label(payload: str) -> str:
    """Вытащить метку из ответа модели.

    Терпит markdown-заборы ```json … ```, лишний текст вокруг JSON,
    `{"label": ...}`, `{"category": ...}` и просто строку-метку.
    Неизвестная метка → `нет_жалобы` (безопасный дефолт), чтобы один
    кривой ответ не ронял весь прогон.
    """
    if not payload or not payload.strip():
        raise ValueError("пустой ответ модели")
    text = payload.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    obj = None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        brace = re.search(r"\{.*\}", text, re.S)
        if brace:
            try:
                obj = json.loads(brace.group(0))
            except json.JSONDecodeError:
                obj = None

    if isinstance(obj, dict):
        for key in ("label", "category", "class", "жалоба", "метка"):
            value = obj.get(key)
            if isinstance(value, str):
                return _canonical(value)
        raise ValueError(f"в JSON нет поля label: {truncate(text, 120)}")
    if isinstance(obj, str):
        return _canonical(obj)

    # последний шанс: метка прямо в тексте
    bare = text.strip().strip('"').strip("'")
    return _canonical(bare)


def _canonical(label: str) -> str:
    """Привести метку к каноническому имени класса."""
    value = normalize_text(label).replace(" ", "_").replace("-", "_").strip("_")
    if value in LABELS:
        return value
    aliases = {
        "нет_жалоб": "нет_жалобы",
        "нет_жалобов": "нет_жалобы",
        "нет": "нет_жалобы",
        "жалоб_нет": "нет_жалобы",
        "без_жалоб": "нет_жалобы",
        "positive": "нет_жалобы",
        "нейтрально": "нет_жалобы",
        "качество": "брак_качество",
        "брак": "брак_качество",
        "описание": "описание_не_соответствует",
        "не_соответствует_описанию": "описание_не_соответствует",
        "поддержка": "продавец_поддержка",
        "продавец": "продавец_поддержка",
    }
    return aliases.get(value, "нет_жалобы")


def build_request_body(text: str, model: str, temperature: float = 0.0) -> dict:
    """Тело запроса к /chat/completions. Отдельно — чтобы тестировать без сети."""
    return {
        "model": model,
        "temperature": temperature,
        "max_tokens": 40,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_PROMPT.format(classes="\n".join(f"- {c}" for c in LABELS), text=text),
            },
        ],
    }


def _http_post_json(url: str, body: dict, headers: dict[str, str], timeout: float) -> dict:
    """POST JSON через urllib. Единственная сетевая точка бэкенда."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — url задан конфигом
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


@dataclass
class LLMBackend:
    """Классификатор через OpenAI-совместимый API.

    `transport` можно подменить в тестах, чтобы проверить разбор ответа без
    сети. По умолчанию — `_http_post_json`.
    """

    name: str = "llm"
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = 30.0
    transport: Callable[[str, dict, dict[str, str], float], dict] = _http_post_json
    calls: int = 0
    errors: list[str] = field(default_factory=list)
    cache: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "LLMBackend":
        """Собрать бэкенд из окружения. Без ключа — `available() == False`."""
        env = os.environ if env is None else env
        return cls(
            api_key=env.get("OPENAI_API_KEY", "").strip(),
            base_url=(env.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            model=env.get("OPENAI_MODEL") or DEFAULT_MODEL,
            timeout=float(env.get("OPENAI_TIMEOUT") or 30),
        )

    def available(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> str:
        if not self.available():
            return "LLM: недоступен — не задан OPENAI_API_KEY"
        return f"LLM: {self.model} @ {self.base_url}"

    def unavailable_reason(self) -> str:
        if self.available():
            return ""
        return (
            "OPENAI_API_KEY не задан: LLM-бэкенд не прогонялся. "
            "Задайте ключ и перезапустите build.py, чтобы получить сравнение."
        )

    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def predict(self, text: str) -> LLMResponse:
        """Разметить один отзыв. Бросает `LLMUnavailable`, если ключа нет."""
        if not self.available():
            raise LLMUnavailable(self.unavailable_reason())
        if text in self.cache:
            return LLMResponse(label=self.cache[text], raw="", latency_ms=0.0, from_cache=True)

        body = build_request_body(text, model=self.model)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        started = time.perf_counter()
        self.calls += 1
        try:
            payload = self.transport(self._url(), body, headers, self.timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
            raise LLMUnavailable(f"сетевая ошибка: {exc}") from exc
        elapsed = (time.perf_counter() - started) * 1000

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            self.errors.append(f"неожиданная структура ответа: {exc}")
            raise LLMUnavailable(f"неожиданная структура ответа: {exc}") from exc

        try:
            label = parse_label(content)
        except ValueError as exc:
            self.errors.append(str(exc))
            return LLMResponse(label="нет_жалобы", raw=str(content), latency_ms=elapsed, ok=False, error=str(exc))

        self.cache[text] = label
        return LLMResponse(label=label, raw=str(content), latency_ms=elapsed)

    def predict_many(self, texts: list[str], progress: Callable[[int, int], None] | None = None) -> list[str]:
        """Последовательная разметка: API не любит параллельные запросы без нужды."""
        out: list[str] = []
        for i, text in enumerate(texts, start=1):
            try:
                out.append(self.predict(text).label)
            except LLMUnavailable:
                out.append("нет_жалобы")
            if progress:
                progress(i, len(texts))
        return out
