"""Чистые функции для расчёта окон загрузки и проверки свежести.

Модуль намеренно не импортирует Airflow: вся арифметика дат тестируется
обычным юнит-тестом, а DAG остаётся тонкой обвязкой.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Union

DateLike = Union[str, date, datetime]


def to_date(value: DateLike) -> date:
    """Приводит строку/дату/дату-время к date.

    Строки принимаются в формате ISO (``2026-11-04`` или ``2026-11-04T03:00:00``),
    именно такой Airflow подставляет в шаблоны ``{{ ds }}`` и ``{{ ts }}``.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError("пустая дата")
    return date.fromisoformat(text[:10])


def partition_window(day: DateLike) -> tuple[datetime, datetime]:
    """Границы суточного партиционирования: [00:00 дня, 00:00 следующего дня)."""
    d = to_date(day)
    start = datetime.combine(d, time.min)
    return start, start + timedelta(days=1)


def incremental_window(ds: DateLike, lookback_days: int = 3) -> tuple[date, date]:
    """Окно пересчёта с учётом опоздавших данных.

    Возвращает ``(start_day, end_day)`` включительно. Берём не только день
    запуска, но и ``lookback_days`` предыдущих: заказы и возвраты на маркетплейсе
    дозаписываются задним числом, и без перекрытия витрина расходится с фактом.
    """
    if lookback_days < 0:
        raise ValueError("lookback_days не может быть отрицательным")
    end = to_date(ds)
    return end - timedelta(days=lookback_days), end


def dates_between(start: DateLike, end: DateLike) -> list[date]:
    """Список дат от start до end включительно."""
    s, e = to_date(start), to_date(end)
    if s > e:
        raise ValueError(f"начало окна ({s}) позже конца ({e})")
    return [s + timedelta(days=i) for i in range((e - s).days + 1)]


def freshness_lag_minutes(last_update: DateLike, now: DateLike) -> float:
    """Отставание данных в минутах (может быть отрицательным при рассинхроне часов)."""
    def _dt(v: DateLike) -> datetime:
        if isinstance(v, datetime):
            return v
        if isinstance(v, date):
            return datetime.combine(v, time.min)
        return datetime.fromisoformat(str(v))

    return (_dt(now) - _dt(last_update)).total_seconds() / 60.0


def is_fresh(last_update: DateLike, now: DateLike, slo_minutes: int = 60) -> tuple[bool, float]:
    """Укладывается ли обновление источника в SLO.

    Возвращает ``(ok, lag_minutes)``. Используется как шлюз: если источник
    отстал сильнее SLO, пайплайн не должен публиковать устаревшую витрину.
    """
    if slo_minutes < 0:
        raise ValueError("slo_minutes не может быть отрицательным")
    lag = freshness_lag_minutes(last_update, now)
    return lag <= slo_minutes, lag


def month_start(day: DateLike) -> date:
    """Первое число месяца, к которому относится дата."""
    d = to_date(day)
    return d.replace(day=1)


def month_label(day: DateLike) -> str:
    """Метка месяца вида ``2026-11`` для партиций и имён файлов."""
    return month_start(day).strftime("%Y-%m")


def open_period_flag(day: DateLike, today: DateLike, settle_days: int = 14) -> bool:
    """Период ещё «дозревает»: возвраты и отмены могут прийти позже.

    Нужен, чтобы не строить окончательные выводы по последним двум неделям.
    """
    d, t = to_date(day), to_date(today)
    return (t - d).days < settle_days