"""Нормализация русского текста отзыва — общая для обоих бэкендов.

Правила и LLM получают один и тот же очищенный вид, чтобы разница в метриках
относилась к классификатору, а не к предобработке.
"""
from __future__ import annotations

import re
import unicodedata

EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U0000FE0F"
    "\U0000200D"
    "]+"
)

NEGATION_CUES: frozenset[str] = frozenset(
    {
        "не", "ни", "нет", "без", "никак", "никакой", "никаких", "совсем",
        "вовсе", "отнюдь", "кроме", "либо", "ничуть",
    }
)

#: хвосты, которые режутся, чтобы «отлично!» и «отлично» считались одинаково
_PUNCT_RE = re.compile(r"[!?…]+")
_SPACES_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[0-9a-zа-яё]+(?:[-'][0-9a-zа-яё]+)*")


def strip_emoji(text: str) -> str:
    """Убрать эмодзи и модификаторы, сохранив текст."""
    return EMOJI_RE.sub(" ", text)


def normalize_text(text: str) -> str:
    """Привести отзыв к виду, удобному для сопоставления.

    - NFKC (ё как отдельная буква не склеивается с е — это делаем отдельно);
    - нижний регистр;
    - `ё` → `е` (в отзывах пишут и так, и так);
    - эмодзи вырезаются;
    - `!`/`?`/`…` → пробел, повторные пробелы схлопываются;
    - кавычки-ёлочки и типографские тире нормализуются.

    Исходный текст не меняется — он хранится рядом и показывается в дашборде.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = strip_emoji(text)
    text = text.replace("«", '"').replace("»", '"').replace("“", '"').replace("”", '"')
    text = text.replace("—", "-").replace("–", "-").replace("−", "-")
    text = _PUNCT_RE.sub(" ", text)
    text = text.replace("...", " ")
    text = _SPACES_RE.sub(" ", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    """Токены нормализованного текста (без пунктуации)."""
    return _TOKEN_RE.findall(normalize_text(text))


def split_sentences(text: str) -> list[str]:
    """Грубое деление на предложения по `.`, `!`, `?` и переводу строки.

    Нужно для окна отрицания: «не брак. всё хорошо» — это два разных
    утверждения, отрицание из первого не должно глушить второе.
    """
    parts = re.split(r"(?<=[.!?])\s+|\n+", normalize_text(text))
    return [p.strip(" .!?") for p in parts if p.strip(" .!?")]


def is_negated(text: str, start: int, window: int = 3) -> bool:
    """Есть ли отрицание в `window` токенах перед позицией `start`.

    Окно ограничено предложением: ищем границу предложения левее `start`
    и смотрим только на токены между ней и совпадением. Само совпадение
    не анализируем — «не пришёл» несёт отрицание внутри фразы.
    """
    if start <= 0:
        return False
    head = text[:start]
    # граница предложения — последний терминатор
    boundary = max(head.rfind(". "), head.rfind("! "), head.rfind("? "), head.rfind("\n"))
    head = head[boundary + 1:] if boundary >= 0 else head
    tail_tokens = _TOKEN_RE.findall(head)[-window:]
    return any(tok in NEGATION_CUES for tok in tail_tokens)


def find_spans(text: str, pattern: re.Pattern[str]) -> list[tuple[int, int, str]]:
    """Все совпадения регулярки: (начало, конец, совпавший фрагмент)."""
    return [(m.start(), m.end(), m.group()) for m in pattern.finditer(text)]


def truncate(text: str, limit: int = 160) -> str:
    """Обрезка для таблиц дашборда — по границе слова."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > limit // 2 else limit].rstrip() + "…"
