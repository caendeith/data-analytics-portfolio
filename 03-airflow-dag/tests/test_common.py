"""Юнит-тесты чистой логики пайплайна.

Не требуют ни Airflow, ни Docker, ни pytest:
    python tests/test_common.py

Если pytest установлен, файл работает и как обычный pytest-модуль.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dags"))

from common.quality import (  # noqa: E402
    CheckResult,
    DataQualityError,
    QualityReport,
    check_freshness,
    check_not_null,
    check_positive,
    check_referential,
    check_rowcount,
    check_rowcount_delta,
    check_share_within,
    check_unique,
    check_within_range,
    raise_if_failed,
    run_checks,
    warn,
)
from common.sql import (  # noqa: E402
    analyze_sql,
    apply_sql_file,
    count_window_sql,
    delete_partition_sql,
    insert_from_select_sql,
    max_updated_at_sql,
    qualified,
    quote_ident,
    refresh_matview_sql,
    upsert_sql,
    window_select_sql,
)
from common.watermark import (  # noqa: E402
    dates_between,
    incremental_window,
    is_fresh,
    month_label,
    open_period_flag,
    partition_window,
    to_date,
)

# ============================================================== watermark

def test_to_date_принимает_строку_дату_и_датувремя():
    assert to_date("2026-11-04") == date(2026, 11, 4)
    assert to_date("2026-11-04T03:00:00+00:00") == date(2026, 11, 4)
    assert to_date(date(2026, 1, 2)) == date(2026, 1, 2)
    assert to_date(datetime(2026, 1, 2, 23, 59)) == date(2026, 1, 2)


def test_to_date_на_пустой_строке_падает():
    try:
        to_date("   ")
    except ValueError:
        return
    raise AssertionError("ожидали ValueError на пустой дате")


def test_partition_window_полуоткрытый_интервал():
    start, end = partition_window("2026-11-04")
    assert start == datetime(2026, 11, 4, 0, 0)
    assert end == datetime(2026, 11, 5, 0, 0)
    assert (end - start).total_seconds() == 86400


def test_incremental_window_включает_lookback():
    start, end = incremental_window("2026-11-04", lookback_days=3)
    assert end == date(2026, 11, 4)
    assert start == date(2026, 11, 1)


def test_incremental_window_ноль_дней_даёт_только_сегодня():
    assert incremental_window("2026-11-04", 0) == (date(2026, 11, 4), date(2026, 11, 4))


def test_incremental_window_отрицательный_lookback_падает():
    try:
        incremental_window("2026-11-04", -1)
    except ValueError:
        return
    raise AssertionError("ожидали ValueError на отрицательном lookback_days")


def test_dates_between_включает_обе_границы():
    got = dates_between("2026-11-01", "2026-11-04")
    assert got == [date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)]


def test_dates_between_на_перевёрнутом_окне_падает():
    try:
        dates_between("2026-11-04", "2026-11-01")
    except ValueError:
        return
    raise AssertionError("ожидали ValueError, когда начало позже конца")


def test_is_fresh_сравнивает_с_slo():
    ok, lag = is_fresh("2026-11-04T00:00:00", "2026-11-04T01:30:00", slo_minutes=120)
    assert ok is True and abs(lag - 90) < 1e-6

    ok, lag = is_fresh("2026-11-04T00:00:00", "2026-11-04T03:00:00", slo_minutes=120)
    assert ok is False and abs(lag - 180) < 1e-6


def test_is_fresh_отрицательное_slo_падает():
    try:
        is_fresh("2026-11-04", "2026-11-04", slo_minutes=-5)
    except ValueError:
        return
    raise AssertionError("ожидали ValueError на отрицательном SLO")


def test_month_label():
    assert month_label("2026-11-04") == "2026-11"
    assert month_label("2026-01-31") == "2026-01"


def test_open_period_flag_отмечает_незрелые_дни():
    assert open_period_flag("2026-11-01", "2026-11-04", settle_days=14) is True
    assert open_period_flag("2026-09-01", "2026-11-04", settle_days=14) is False


# ============================================================== quality

def _orders() -> pd.DataFrame:
    return pd.DataFrame({
        "order_id": [1, 2, 3, 4],
        "order_date": ["2026-11-01", "2026-11-01", "2026-11-02", "2026-11-02"],
        "sku": ["SKU-1", "SKU-2", "SKU-1", "SKU-3"],
        "amount": [1000.0, 2500.0, 800.0, 4200.0],
        "units": [1, 2, 1, 1],
        "is_canceled": [False, False, True, False],
        "is_returned": [False, False, False, False],
        "updated_at": pd.to_datetime(["2026-11-04 01:00", "2026-11-04 01:30",
                                      "2026-11-04 02:00", "2026-11-04 02:30"]),
    })


def test_rowcount_и_коридор():
    df = _orders()
    assert check_rowcount(df, 4).passed is True
    assert check_rowcount(df, 5).passed is False
    assert check_rowcount_delta(df, 1, 10).passed is True
    assert check_rowcount_delta(df, 10, 20).passed is False


def test_not_null_ловит_пустые_и_отсутствующие_колонки():
    df = _orders()
    assert check_not_null(df, ["order_id", "amount"]).passed is True

    broken = df.copy()
    broken.loc[0, "amount"] = None
    res = check_not_null(broken, ["amount"])
    assert res.passed is False and "amount" in res.detail

    assert check_not_null(df, ["нет_такой"]).passed is False


def test_unique_ловит_дубли():
    df = _orders()
    assert check_unique(df, ["order_id"]).passed is True
    res = check_unique(df, ["order_date"])
    assert res.passed is False and "2" in res.detail


def test_positive_и_диапазон():
    df = _orders()
    assert check_positive(df, "amount").passed is True

    broken = df.copy()
    broken.loc[1, "amount"] = -5
    assert check_positive(broken, "amount").passed is False
    assert check_within_range(broken, "amount", 0, 10000).passed is False
    assert check_within_range(df, "amount", 0, 10000).passed is True


def test_referential_находит_сирот():
    df = _orders()
    catalog = pd.DataFrame({"sku": ["SKU-1", "SKU-2"]})
    res = check_referential(df, catalog, "sku", "sku")
    assert res.passed is False and "SKU-3" in res.detail

    full = pd.DataFrame({"sku": ["SKU-1", "SKU-2", "SKU-3"]})
    assert check_referential(df, full, "sku", "sku").passed is True


def test_freshness_по_максимальной_метке():
    df = _orders()
    ok = check_freshness(df, "updated_at", datetime(2026, 11, 4, 4, 0), max_lag_hours=3)
    assert ok.passed is True

    stale = check_freshness(df, "updated_at", datetime(2026, 11, 4, 12, 0), max_lag_hours=3)
    assert stale.passed is False


def test_share_within_доля_единиц():
    df = _orders()
    assert check_share_within(df, "is_canceled", 0.0, 0.5).passed is True
    assert check_share_within(df, "is_canceled", 0.5, 1.0).passed is False


def test_run_checks_и_падение_на_ошибке():
    df = _orders()
    report = run_checks([check_rowcount(df, 4), check_unique(df, ["order_id"])])
    assert report.ok is True and len(report.results) == 2
    assert "провалов: 0" in report.to_text()

    bad = run_checks([check_rowcount(df, 100)])
    assert bad.ok is False
    try:
        raise_if_failed(bad)
    except DataQualityError as exc:
        assert "rowcount" in str(exc)
    else:
        raise AssertionError("ожидали DataQualityError")


def test_warn_не_останавливает_пайплайн():
    report = QualityReport().add(warn(CheckResult("share", False, "доля вне коридора")))
    assert report.ok is True
    assert len(report.warnings) == 1
    assert report.failures == []
    assert report.summary()["warned"] == 1


def test_report_summary_перечисляет_упавшие_проверки():
    df = _orders()
    report = run_checks([check_rowcount(df, 100), check_unique(df, ["order_date"])])
    summary = report.summary()
    assert summary["ok"] is False
    assert summary["failed"] == 2
    assert set(summary["checks"]) == {"rowcount", "unique"}


# ============================================================== sql

def test_quote_ident_отсекает_инъекции():
    assert quote_ident("orders_daily") == '"orders_daily"'
    for bad in ["orders; DROP TABLE x", "1table", "", 'ord"ers', "schema.table"]:
        try:
            quote_ident(bad)
        except ValueError:
            continue
        raise AssertionError(f"ожидали ValueError для {bad!r}")


def test_qualified():
    assert qualified("mart", "daily_revenue") == '"mart"."daily_revenue"'


def test_delete_partition_возвращает_параметры_а_не_склейку():
    sql, args = delete_partition_sql("stg", "orders_daily", "order_date", date(2026, 11, 4))
    assert sql == 'DELETE FROM "stg"."orders_daily" WHERE "order_date" = %s'
    assert args == [date(2026, 11, 4)]
    assert "2026-11-04" not in sql


def test_insert_from_select_перечисляет_колонки():
    sql, args = insert_from_select_sql(
        "stg", "orders_daily", "raw", "orders",
        ["order_id", "amount"], "order_date", date(2026, 11, 4),
    )
    assert 'INSERT INTO "stg"."orders_daily" ("order_id", "amount")' in sql
    assert 'SELECT "order_id", "amount" FROM "raw"."orders"' in sql
    assert args == [date(2026, 11, 4)]


def test_upsert_делает_do_update_и_do_nothing():
    sql, _ = upsert_sql("mart", "daily_revenue", ["order_date", "revenue"], ["order_date"])
    assert "ON CONFLICT" in sql and "DO UPDATE SET" in sql
    assert '"revenue" = EXCLUDED."revenue"' in sql

    sql, _ = upsert_sql("mart", "daily_revenue", ["order_date"], ["order_date"])
    assert "DO NOTHING" in sql


def test_window_and_count_sql():
    sql, args = window_select_sql("stg", "orders_daily", "order_date",
                                  date(2026, 11, 1), date(2026, 11, 4))
    assert "BETWEEN %s AND %s" in sql and len(args) == 2

    sql, args = count_window_sql("raw", "orders", "order_date", date(2026, 11, 4))
    assert "COUNT(*)" in sql and args == [date(2026, 11, 4)]


def test_max_updated_at_и_analyze():
    sql, args = max_updated_at_sql("raw", "orders", "updated_at")
    assert 'MAX("updated_at")' in sql and args == []

    sql, args = analyze_sql("mart", "daily_revenue")
    assert sql == 'ANALYZE "mart"."daily_revenue"'


def test_refresh_matview_concurrently():
    sql, _ = refresh_matview_sql("bi", "revenue_daily", concurrently=True)
    assert sql == 'REFRESH MATERIALIZED VIEW CONCURRENTLY "bi"."revenue_daily"'

    sql, _ = refresh_matview_sql("bi", "revenue_daily", concurrently=False)
    assert "CONCURRENTLY" not in sql


def test_apply_sql_file_проверяет_параметры():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "q.sql"
        path.write_text("SELECT 1;", encoding="utf-8")
        text, params = apply_sql_file(str(path), {"ds": "2026-11-04"})
        assert text == "SELECT 1;" and params == {"ds": "2026-11-04"}

        try:
            apply_sql_file(str(path), {"os_environ": "x"})
        except ValueError:
            return
        raise AssertionError("ожидали ValueError на неизвестном параметре шаблона")


# ============================================================== runner

def main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001 - раннер должен показать всё
            failed.append((name, exc))
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")

    print(f"\nитого: {len(tests) - len(failed)} из {len(tests)} прошли")
    if failed:
        print("провалились:")
        for name, exc in failed:
            print(f"  - {name}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())