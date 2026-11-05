"""Проверки качества данных, которые выполняются между слоями пайплайна.

Идея: любая таска, забирающая данные, обязана вернуть не только данные, но и
вердикт «им можно верить». Проверки описаны декларативно и не зависят от
Airflow, поэтому их можно прогонять в юнит-тестах на настоящих DataFrame.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

import pandas as pd


class DataQualityError(RuntimeError):
    """Данные не прошли проверки — пайплайн обязан остановиться, а не публиковать мусор."""

    def __init__(self, report: "QualityReport") -> None:
        self.report = report
        super().__init__(report.to_text())


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    severity: str = "error"  # error | warn

    def __str__(self) -> str:  # pragma: no cover - удобство логов
        mark = "OK  " if self.passed else ("WARN" if self.severity == "warn" else "FAIL")
        return f"[{mark}] {self.name}: {self.detail}"


@dataclass
class QualityReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> "QualityReport":
        self.results.append(result)
        return self

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.severity == "error"]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.severity == "warn"]

    @property
    def ok(self) -> bool:
        return not self.failures

    def to_text(self) -> str:
        if not self.results:
            return "проверок не выполнялось"
        head = f"проверок: {len(self.results)}, провалов: {len(self.failures)}, предупреждений: {len(self.warnings)}"
        return "\n".join([head] + [str(r) for r in self.results])

    def summary(self) -> dict[str, Any]:
        return {
            "total": len(self.results),
            "failed": len(self.failures),
            "warned": len(self.warnings),
            "ok": self.ok,
            "checks": [r.name for r in self.failures],
        }


# ------------------------------------------------------------------ отдельные проверки

def check_not_empty(df: pd.DataFrame, name: str = "not_empty") -> CheckResult:
    return CheckResult(name, len(df) > 0, f"строк: {len(df)}")


def check_rowcount(df: pd.DataFrame, min_rows: int, name: str = "rowcount") -> CheckResult:
    n = len(df)
    return CheckResult(name, n >= min_rows, f"строк: {n}, ожидалось не меньше {min_rows}")


def check_rowcount_delta(
    df: pd.DataFrame,
    min_rows: int,
    max_rows: int,
    name: str = "rowcount_delta",
) -> CheckResult:
    """Защита от «тихой» поломки загрузки: объём должен быть в коридоре.

    Пустая витрина и витрина, выросшая в десять раз, одинаково подозрительны.
    """
    n = len(df)
    ok = min_rows <= n <= max_rows
    return CheckResult(name, ok, f"строк: {n}, ожидался коридор [{min_rows}; {max_rows}]")


def check_not_null(df: pd.DataFrame, columns: Sequence[str], name: str = "not_null") -> CheckResult:
    missing_cols = [c for c in columns if c not in df.columns]
    if missing_cols:
        return CheckResult(name, False, f"нет колонок: {', '.join(missing_cols)}")
    nulls = {c: int(df[c].isna().sum()) for c in columns}
    bad = {c: v for c, v in nulls.items() if v}
    return CheckResult(name, not bad, f"пустых значений: {bad or 'нет'}")


def check_unique(df: pd.DataFrame, columns: Sequence[str], name: str = "unique") -> CheckResult:
    missing_cols = [c for c in columns if c not in df.columns]
    if missing_cols:
        return CheckResult(name, False, f"нет колонок: {', '.join(missing_cols)}")
    dupes = int(df.duplicated(subset=list(columns)).sum())
    return CheckResult(name, dupes == 0, f"дублей по ({', '.join(columns)}): {dupes}")


def check_positive(df: pd.DataFrame, column: str, name: str = "positive") -> CheckResult:
    if column not in df.columns:
        return CheckResult(name, False, f"нет колонки {column}")
    bad = int((pd.to_numeric(df[column], errors="coerce") < 0).sum())
    return CheckResult(name, bad == 0, f"отрицательных значений в {column}: {bad}")


def check_within_range(
    df: pd.DataFrame,
    column: str,
    low: float,
    high: float,
    name: str = "within_range",
) -> CheckResult:
    if column not in df.columns:
        return CheckResult(name, False, f"нет колонки {column}")
    s = pd.to_numeric(df[column], errors="coerce")
    bad = int(((s < low) | (s > high)).sum())
    return CheckResult(name, bad == 0, f"{column} вне [{low}; {high}]: {bad}")


def check_referential(
    df: pd.DataFrame,
    reference: pd.DataFrame,
    column: str,
    ref_column: str,
    name: str = "referential",
) -> CheckResult:
    """Проверка ссылочной целостности без обращения к БД."""
    if column not in df.columns or ref_column not in reference.columns:
        return CheckResult(name, False, "нет нужных колонок для проверки ссылок")
    known = set(reference[ref_column].dropna().unique())
    orphans = sorted(set(df[column].dropna().unique()) - known)
    shown = ", ".join(str(x) for x in orphans[:5])
    more = f" и ещё {len(orphans) - 5}" if len(orphans) > 5 else ""
    return CheckResult(name, not orphans, f"сирот: {len(orphans)}{': ' + shown + more if orphans else ''}")


def check_freshness(
    df: pd.DataFrame,
    column: str,
    now: datetime,
    max_lag_hours: float,
    name: str = "freshness",
) -> CheckResult:
    """Данные не должны отставать сильнее допустимого."""
    if column not in df.columns or df.empty:
        return CheckResult(name, False, f"нет данных для проверки свежести ({column})")
    ts = pd.to_datetime(df[column], errors="coerce").max()
    if pd.isna(ts):
        return CheckResult(name, False, f"не удалось разобрать {column}")
    lag_hours = (now - ts.to_pydatetime()).total_seconds() / 3600.0
    return CheckResult(name, lag_hours <= max_lag_hours,
                       f"отставание {lag_hours:.1f} ч, допустимо {max_lag_hours:.1f} ч")


def check_share_within(
    df: pd.DataFrame,
    column: str,
    low: float,
    high: float,
    name: str = "share_within",
) -> CheckResult:
    """Доля единиц в колонке должна лежать в коридоре (например, доля возвратов)."""
    if column not in df.columns or df.empty:
        return CheckResult(name, False, f"нет данных в {column}")
    share = float(pd.to_numeric(df[column], errors="coerce").fillna(0).mean())
    return CheckResult(name, low <= share <= high,
                       f"доля {share:.3f}, ожидался коридор [{low}; {high}]")


def warn(result: CheckResult) -> CheckResult:
    """Понижает строгость проверки: падение не останавливает пайплайн."""
    result.severity = "warn"
    return result


# ------------------------------------------------------------------ запуск набора

def run_checks(checks: Iterable[CheckResult]) -> QualityReport:
    report = QualityReport()
    for c in checks:
        report.add(c)
    return report


def raise_if_failed(report: QualityReport) -> None:
    if not report.ok:
        raise DataQualityError(report)