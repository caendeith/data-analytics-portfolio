"""Классификатор на правилах: ключевые слова + регулярки + окно отрицания.

Осознанно базовый: это базовая линия, с которой сравнивается LLM. Никаких
внешних зависимостей, только stdlib и `reviewlab.text`.

Устройство:
1. текст нормализуется (`normalize_text`) — эмодзи, регистр, пунктуация;
2. для каждого класса есть список регулярок; каждая даёт вес;
3. совпадение проверяется на отрицание в окне перед ним: «не брак» не
   добавляет очков классу `брак_качество`, но «не пришёл вовремя» —
   добавляет `доставка` (отрицание внутри самой фразы, а не перед ней);
4. берётся класс с максимальным весом; если вес ниже порога — `нет_жалобы`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dataset import LABELS
from .text import find_spans, normalize_text

#: класс -> (вес, регулярка). Вес приблизительно отражает специфичность.
RULES: dict[str, tuple[tuple[float, str], ...]] = {
    "размер": (
        (3.0, r"размер\w*\s+не\s+(?:подош\w*|совпа\w*|подход\w*)"),
        (2.5, r"маломер\w*|большемер\w*"),
        (2.4, r"пришл[оаи]\s+[smlx]{1,3}\s+вместо|вместо\s+заказанн\w*\s+[smlx]{1,3}"),
        (2.2, r"слишком\s+(?:велик\w*|больш\w*|мал\w*|тесн\w*)"),
        (2.0, r"не\s+подош\w*\s+(?:размер|по\s+размеру)"),
        (2.0, r"(?:взял|брал|заказывал)\w*\s+(?:свой\s+)?(?:размер\s+)?[smlx]{1,3}\b"),
        (1.8, r"(?:на\s+)?(?:два|пару|полтора)?\s*размер\w*\s+(?:меньше|больше)"),
        (1.8, r"сантиметров\s+(?:на\s+)?(?:десять|пять|два)|см\s+(?:короче|длиннее)"),
        (1.6, r"не\s+(?:мой|тот)\s+(?:размер|крой)"),
        (1.4, r"жм[её]т|тесн\w*\s+в\s+(?:плеч\w*|пояс\w*)"),
        (1.2, r"длин\w*\s+не\s+та|рукава\s+коротк\w*|коротк\w*\s+(?:рукав|длин)"),
        (1.2, r"(?:размер|сетк)\w*\s+не\s+совпа\w*"),
        (0.8, r"\bразмер\b"),
    ),
    "брак_качество": (
        (2.6, r"брак\w*|бракованн\w*"),
        (2.2, r"дефект\w*|скол\w*|царапин\w*|трещин\w*|пот[её]ртост\w*"),
        (2.0, r"развалил\w*|порвал\w*|треснул\w*|облезл\w*|ржавчин\w*"),
        (1.8, r"шов\w*\s+крив\w*|нитк\w*\s+торчат\w*"),
        (1.8, r"не\s+нов\w*|битый\s+экран|явно\s+б/у"),
        (1.4, r"качеств\w*\s+так\s+себе|тонк\w*\s+ткан\w*"),
    ),
    "доставка": (
        (2.8, r"доставк\w*|доставил\w*|курьер\w*|посылк\w*"),
        (2.4, r"задерж\w*|опозда\w*|перенесл\w*|не\s+приш\w*\s+вовремя"),
        (2.0, r"трек\w*|склад\w*|пункт\w*\s+выдачи"),
        (1.8, r"ждал\w*|ждала|ждать"),
        (1.6, r"не\s+дождал\w*|потерял\w*|отменил\w*"),
    ),
    "цена": (
        (2.6, r"цен[аыуе]\w*|стоит\w*\s+своих\s+денег|переплат\w*"),
        (2.2, r"дорог\w*|завышен\w*|дешевле|скидк\w*"),
        (1.6, r"не\s+стоит\w*|не\s+соответству\w*\s+цен[еы]"),
        (1.4, r"акци\w*|распродаж\w*"),
    ),
    "упаковка": (
        (2.8, r"упаковк\w*|упаковал\w*"),
        (2.4, r"коробк\w*\s+(?:помят\w*|порван\w*|развалил\w*|вскрыт\w*|мят\w*)"),
        (2.2, r"плёнк\w*|пленк\w*|пузырчат\w*|наполнител\w*|скотч\w*"),
        (2.0, r"помят\w*|мят\w*|порван\w*|пломб\w*"),
        (1.4, r"в\s+пыли|болтал\w*"),
    ),
    "описание_не_соответствует": (
        (3.0, r"описани\w*\s+не\s+соответству\w*"),
        (2.6, r"не\s+соответству\w*\s+(?:описани\w*|характеристик\w*|фото)"),
        (2.4, r"заявлен\w*|обещал\w*|в\s+комплекте"),
        (2.2, r"не\s+совпа\w*|не\s+то,?\s+что|пришл[оаи]\s+не\s+то"),
        (2.0, r"на\s+фото|на\s+картинке|в\s+жизни"),
        (1.8, r"состав\w*\s+хлопок|синтетик\w*"),
        (1.4, r"друг\w*\s+цвет|друг\w*\s+фасон"),
    ),
    "продавец_поддержка": (
        (3.0, r"продав\w*\s+(?:не\s+отвеча\w*|молчи\w*|пропал\w*|обещал\w*)"),
        (2.8, r"поддержк\w*|техподдержк\w*|оператор\w*|на\s+претензи\w*"),
        (2.2, r"не\s+отвеча\w*|игнорир\w*|шаблон\w*|бот\w*\s+по\s+кругу"),
        (2.0, r"возврат\w*\s+не\s+оформ\w*|вопрос\s+не\s+реш\w*"),
        (1.6, r"чат\w*|сообщени\w*"),
    ),
}

#: положительные маркеры: ими «нет_жалобы» отделяется от жалобы. Это честная
#: слабость подхода — лексика хвалы и лексика жалобы пересекаются («доставка
#: быстрая» vs «доставка задержалась»), поэтому нужен отдельный словарь.
POSITIVE_RULES: tuple[tuple[float, str], ...] = (
    (2.0, r"отличн\w*|прекрасн\w*|супер|идеальн\w*"),
    (1.8, r"понравил\w*|довольн\w*|рекомендую|спасибо"),
    (1.6, r"вс[её]\s+(?:хорошо|отлично|честно|на\s+уровне|совпал\w*|работает)"),
    (1.6, r"нет\s+претензи\w*|не\s+брак|без\s+нарекани\w*"),
    (1.4, r"быстр\w*|раньше\s+срока|точн\w*|в\s+размер|как\s+на\s+фото"),
    (1.2, r"пол[её]т\s+нормальн\w*|цел\w*|аккуратн\w*|вежлив\w*"),
    (1.0, r"хорош\w*|нормальн\w*|качеств\w*\s+на\s+уровне"),
)

#: ниже этого веса класс не назначается
MIN_SCORE = 1.5

#: если положительный вес перекрывает лучшую жалобу с этим запасом — «нет жалобы»
POSITIVE_MARGIN = 1.15


@dataclass
class Prediction:
    """Предсказание одного отзыва."""

    label: str
    score: float
    scores: dict[str, float] = field(default_factory=dict)
    matched: list[str] = field(default_factory=list)


_COMPILED: dict[str, tuple[tuple[float, re.Pattern[str]], ...]] = {
    label: tuple((weight, re.compile(pattern)) for weight, pattern in rules)
    for label, rules in RULES.items()
}
_POSITIVE_COMPILED = tuple((weight, re.compile(pattern)) for weight, pattern in POSITIVE_RULES)

_NO_COMPLAINT = "нет_жалобы"


def score_text(text: str) -> tuple[dict[str, float], list[str]]:
    """Посчитать веса классов для нормализованного текста.

    Возвращает (очки по классам, список сработавших фрагментов). Фрагменты
    нужны для объяснения решения в дашборде — «почему». Очки для
    `нет_жалобы` — это положительные маркеры минус лучшая жалоба; считается
    в `classify`, здесь возвращаются только классы-жалобы и `нет_жалобы`
    как положительный счёт.
    """
    norm = normalize_text(text)
    scores: dict[str, float] = {}
    matched: list[str] = []
    for label, compiled in _COMPILED.items():
        total = 0.0
        for weight, pattern in compiled:
            for start, _end, fragment in find_spans(norm, pattern):
                if _is_negated_span(norm, start):
                    continue
                total += weight
                matched.append(fragment.strip())
        if total > 0:
            scores[label] = round(total, 2)

    positive = 0.0
    for weight, pattern in _POSITIVE_COMPILED:
        for start, _end, fragment in find_spans(norm, pattern):
            if _is_negated_span(norm, start):
                continue
            positive += weight
            matched.append(fragment.strip())
    if positive > 0:
        scores[_NO_COMPLAINT] = round(positive, 2)
    return scores, matched


def _is_negated_span(norm: str, start: int, window: int = 3) -> bool:
    """Отрицание непосредственно перед совпадением, внутри предложения."""
    from .text import is_negated

    return is_negated(norm, start, window=window)


def classify(text: str) -> Prediction:
    """Разметить один отзыв: класс с максимальным весом или `нет_жалобы`."""
    scores, matched = score_text(text)
    positive = scores.get(_NO_COMPLAINT, 0.0)
    complaints = {k: v for k, v in scores.items() if k != _NO_COMPLAINT}
    if not complaints:
        return Prediction(label=_NO_COMPLAINT, score=positive, scores=scores, matched=matched[:6])

    best = max(complaints, key=lambda k: (complaints[k], -LABELS.index(k)))
    top = complaints[best]
    if top < MIN_SCORE or positive >= top * POSITIVE_MARGIN:
        return Prediction(label=_NO_COMPLAINT, score=positive, scores=scores, matched=matched[:6])
    return Prediction(label=best, score=top, scores=scores, matched=matched[:6])


def classify_many(texts: list[str]) -> list[Prediction]:
    """Разметить список отзывов подряд."""
    return [classify(t) for t in texts]


class RulesBackend:
    """Обёртка-бэкенд с тем же интерфейсом, что у LLM."""

    name = "rules"

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return "правила: ключевые слова, регулярки, окно отрицания"

    def predict(self, text: str) -> str:
        return classify(text).label

    def predict_many(self, texts: list[str]) -> list[str]:
        return [p.label for p in classify_many(texts)]
