"""Метрики качества классификации: без sklearn, чтобы не тянуть зависимость.

Считается то, что реально нужно для отчёта: точность, macro-F1, матрица ошибок
и разбор самых частых путаниц классов. Ничего лишнего.
"""

from __future__ import annotations

from collections import Counter, defaultdict


def confusion_matrix(golds: list[str], preds: list[str], labels: tuple[str, ...]) -> list[list[int]]:
    """Матрица «gold × предсказание», строки — истина, столбцы — ответ модели."""
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for gold, pred in zip(golds, preds):
        if gold not in index or pred not in index:
            raise ValueError(f"неизвестная метка: gold={gold!r} pred={pred!r}")
        matrix[index[gold]][index[pred]] += 1
    return matrix


def accuracy(golds: list[str], preds: list[str]) -> float:
    if not golds:
        raise ValueError("пустой набор")
    return sum(g == p for g, p in zip(golds, preds)) / len(golds)


def per_class(golds: list[str], preds: list[str], labels: tuple[str, ...]) -> list[dict]:
    """Precision / recall / F1 по каждому классу + support.

    Нулевой знаменатель даёт 0.0, а не падение: класс мог ни разу не встретиться
    в предсказаниях, и это нормальная ситуация, а не ошибка.
    """
    tp = Counter()
    fp = Counter()
    fn = Counter()
    support = Counter(golds)

    for gold, pred in zip(golds, preds):
        if gold == pred:
            tp[gold] += 1
        else:
            fp[pred] += 1
            fn[gold] += 1

    rows = []
    for label in labels:
        precision = tp[label] / (tp[label] + fp[label]) if tp[label] + fp[label] else 0.0
        recall = tp[label] / (tp[label] + fn[label]) if tp[label] + fn[label] else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        rows.append(
            {
                "label": label,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "support": support[label],
                "tp": tp[label],
                "fp": fp[label],
                "fn": fn[label],
            }
        )
    return rows


def macro_f1(rows: list[dict]) -> float:
    """Средний F1 по классам без веса на размер класса.

    Именно macro, а не accuracy: класс `нет_жалобы` занимает треть выборки,
    и на accuracy модель, всегда отвечающая «нет жалобы», выглядела бы неплохо.
    """
    if not rows:
        return 0.0
    return sum(r["f1"] for r in rows) / len(rows)


def top_confusions(golds: list[str], preds: list[str], limit: int = 8) -> list[dict]:
    """Самые частые пары «ожидали X — ответили Y»."""
    pairs = Counter((g, p) for g, p in zip(golds, preds) if g != p)
    return [
        {"gold": gold, "pred": pred, "count": count}
        for (gold, pred), count in pairs.most_common(limit)
    ]


def errors_by_class(golds: list[str], preds: list[str]) -> dict[str, int]:
    """Сколько ошибок приходится на каждый истинный класс."""
    errors = defaultdict(int)
    for gold, pred in zip(golds, preds):
        if gold != pred:
            errors[gold] += 1
    return dict(errors)


def worst_class(rows: list[dict]) -> dict:
    """Класс с самым низким F1 среди тех, что вообще встречались в выборке."""
    present = [r for r in rows if r["support"] > 0]
    if not present:
        raise ValueError("нет классов с ненулевым support")
    return min(present, key=lambda r: (r["f1"], -r["support"]))
