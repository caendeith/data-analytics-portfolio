"""Чистые функции расчётов для on-chain дашборда.

Вынесены отдельно, чтобы их можно было тестировать без сети, без БД
и без чтения JSON-снимков.
"""

from __future__ import annotations


def pct_change(old: float, new: float) -> float | None:
    """Относительное изменение в процентах. None, если база нулевая/отсутствует."""
    if old is None or new is None:
        return None
    if old == 0:
        return None
    return (new - old) / old * 100.0


def share(part: float, total: float) -> float | None:
    """Доля в процентах."""
    if total is None or total == 0 or part is None:
        return None
    return part / total * 100.0


def concentration_hhi(values: list[float]) -> float:
    """Индекс Херфиндаля—Хиршмана по долям (0..10000).

    0 — идеально распределённый рынок, 10000 — монополия одного участника.
    """
    vals = [v for v in values if v and v > 0]
    total = sum(vals)
    if total <= 0:
        return 0.0
    return sum((v / total * 100.0) ** 2 for v in vals)


def top_n_share(values: list[float], n: int) -> float | None:
    """Доля (в процентах) n крупнейших участников в общей сумме."""
    vals = sorted((v for v in values if v and v > 0), reverse=True)
    total = sum(vals)
    if total <= 0:
        return None
    return sum(vals[:n]) / total * 100.0


def nearest_before_index(points: list[tuple[int, float]], target_ts: int) -> int | None:
    """Индекс последней точки, у которой время <= target_ts.

    Нужен, чтобы считать «изменение за 30 дней» по дневному ряду,
    где точки могут пропускать отдельные дни.
    """
    found = None
    for i, (ts, _) in enumerate(points):
        if ts <= target_ts:
            found = i
        else:
            break
    return found


def fmt_usd(value: float | None) -> str:
    """Человеческий формат суммы: 1.23T, 456.7B, 12.3M, 45.6K."""
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    v = abs(value)
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= limit:
            return f"{sign}{v / limit:.2f}{suffix}"
    return f"{sign}{v:.0f}"


def fmt_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:+.{digits}f}%"
