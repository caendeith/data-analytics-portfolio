"""Ежедневный пайплайн витрин маркетплейса.

Что демонстрирует этот DAG:
  * идемпотентную загрузку — delete-insert по суточным партициям, повторный
    запуск за тот же день не плодит строк;
  * пересчёт с lookback-окном: заказы и возвраты на маркетплейсе дозаписываются
    задним числом на 1–14 дней, поэтому вчерашняя партиция ещё не финальна;
  * проверки качества данных между слоями — пайплайн падает на гейте, а не
    публикует мусор в BI;
  * шлюз по свежести источника: витрина не обновляется на устаревших данных;
  * ретраи с экспоненциальным бэкоффом, SLA, алерты в вебхук.

Архитектура (ELT внутри одного хранилища — так `INSERT ... SELECT` остаётся
обычным запросом, без перекачки данных через драйвер):

    raw.orders  ->  stg.orders_daily  ->  mart.daily_revenue
                                     ->  mart.sku_abc
                                     ->  bi.revenue_daily (matview)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airflow.decorators import dag, task, task_group
from airflow.models import Variable
from airflow.operators.empty import EmptyOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

from common.quality import (
    check_freshness,
    check_not_null,
    check_positive,
    check_rowcount,
    check_rowcount_delta,
    check_share_within,
    check_unique,
    raise_if_failed,
    run_checks,
    warn,
)
from common.sql import (
    analyze_sql,
    apply_sql_file,
    count_window_sql,
    delete_partition_sql,
    insert_from_select_sql,
    max_updated_at_sql,
    refresh_matview_sql,
    window_select_sql,
)
from common.watermark import dates_between, incremental_window, is_fresh

log = logging.getLogger(__name__)

# один коннект: и raw, и stg, и mart живут в одном хранилище
DWH_CONN = "dwh"

DAG_DIR = Path(__file__).resolve().parent
SQL_DIR = DAG_DIR / "sql"

OWNER = "data-platform"
ALERT_WEBHOOK_VAR = "marketplace_alerts_webhook"

default_args = {
    "owner": OWNER,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
    "execution_timeout": timedelta(minutes=25),
    "depends_on_past": False,
    "sla": timedelta(hours=3),
}

STAGING_COLUMNS = [
    "order_id", "order_date", "sku", "category", "region",
    "amount", "units", "is_canceled", "is_returned", "updated_at",
]


def _send_alert(subject: str, body: str) -> None:
    """Отправка алерта в вебхук.

    Адрес берётся из Variable, а не из кода: секреты не должны жить в DAG-файле.
    Если вебхук не настроен — пишем в лог и не роняем обработчик ошибки.
    """
    url = Variable.get(ALERT_WEBHOOK_VAR, default_var=None)
    if not url:
        log.warning("алерт не отправлен, %s не задан: %s", ALERT_WEBHOOK_VAR, subject)
        return
    from airflow.providers.http.hooks.http import HttpHook  # локальный импорт

    try:
        HttpHook(method="POST", http_conn_id="alerts").run(
            endpoint=url, json={"text": f"*{subject}*\n{body}"}
        )
    except Exception:  # алерт не должен маскировать исходную ошибку пайплайна
        log.exception("не удалось отправить алерт: %s", subject)


def alert_on_failure(context: dict) -> None:
    ti = context.get("task_instance")
    _send_alert(
        f"DAG {context['dag'].dag_id} упал",
        f"таска: {ti.task_id}\nлоги: {ti.log_url}\n"
        f"попытка: {ti.try_number}\nисключение: {context.get('exception')}",
    )


def alert_on_sla_miss(dag, task_list, blocking_task_list, slas, blocking_tis) -> None:
    _send_alert(
        f"SLA нарушен в {dag.dag_id}",
        "таски: " + ", ".join(sorted({t.task_id for t in task_list})),
    )


@dag(
    dag_id="marketplace_daily_revenue",
    description="Витрины выручки и ABC-анализа SKU по данным маркетплейса",
    schedule="0 3 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["marketplace", "daily", "finance"],
    doc_md=__doc__,
    params={"lookback_days": 3, "min_rows_staging": 5_000, "slo_minutes": 180},
    on_failure_callback=alert_on_failure,
    sla_miss_callback=alert_on_sla_miss,
)
def marketplace_daily_revenue():
    @task(retries=1)
    def check_source_freshness(ds: str | None = None, params: dict | None = None) -> dict:
        """Шлюз по свежести: если источник отстал сильнее SLO — падаем.

        Падаем, а не «пропускаем тихо»: устаревшая витрина опаснее её отсутствия,
        потому что по ней принимают решения.
        """
        params = params or {}
        hook = PostgresHook(postgres_conn_id=DWH_CONN)
        sql, args = max_updated_at_sql("raw", "orders", "updated_at")
        row = hook.get_first(sql, parameters=args)
        last_update = row[0] if row else None
        if last_update is None:
            raise ValueError("источник raw.orders не вернул ни одной метки updated_at")

        now = datetime.now(timezone.utc)
        ok, lag_minutes = is_fresh(last_update, now, slo_minutes=int(params.get("slo_minutes", 180)))
        if not ok:
            raise ValueError(
                f"источник отстал на {lag_minutes:.0f} мин при SLO "
                f"{params.get('slo_minutes')} мин — витрину не обновляем"
            )
        log.info("источник свежий, отставание %.0f мин", lag_minutes)
        return {"last_update": last_update.isoformat(), "lag_minutes": round(lag_minutes, 1)}

    @task_group(group_id="staging")
    def staging(freshness: dict) -> dict:
        @task
        def load_staging(freshness: dict, ds: str | None = None, params: dict | None = None) -> dict:
            """Перезаливает суточные партиции окна: delete-insert, идемпотентно."""
            log.info("загрузка стартует, источник обновлён %s", freshness.get("last_update"))
            params = params or {}
            start_day, end_day = incremental_window(ds, int(params.get("lookback_days", 3)))
            days = dates_between(start_day, end_day)
            hook = PostgresHook(postgres_conn_id=DWH_CONN)

            loaded = 0
            for day in days:
                count_sql, count_args = count_window_sql("raw", "orders", "order_date", day)
                rows_in_source = hook.get_first(count_sql, parameters=count_args)[0]

                delete_sql, delete_args = delete_partition_sql("stg", "orders_daily", "order_date", day)
                hook.run(delete_sql, parameters=delete_args, autocommit=True)

                if not rows_in_source:
                    log.warning("за %s в источнике нет строк — партиция очищена", day)
                    continue

                insert_sql, _ = insert_from_select_sql(
                    "stg", "orders_daily", "raw", "orders",
                    STAGING_COLUMNS, "order_date", day,
                )
                hook.run(insert_sql, parameters=[day], autocommit=True)
                loaded += int(rows_in_source)
                log.info("загружено %s строк за %s", rows_in_source, day)

            return {"days": [d.isoformat() for d in days], "rows_loaded": loaded}

        @task
        def quality_staging(ds: str | None = None, params: dict | None = None) -> dict:
            """Читает staging обратно и проверяет его теми же функциями, что и в тестах."""
            params = params or {}
            start_day, end_day = incremental_window(ds, int(params.get("lookback_days", 3)))
            hook = PostgresHook(postgres_conn_id=DWH_CONN)
            sql, args = window_select_sql("stg", "orders_daily", "order_date", start_day, end_day)
            df = hook.get_pandas_df(sql, parameters=args)

            report = run_checks([
                check_rowcount(df, int(params.get("min_rows_staging", 5_000))),
                check_not_null(df, ["order_id", "order_date", "sku", "amount"]),
                check_unique(df, ["order_id"]),
                check_positive(df, "amount"),
                warn(check_share_within(df, "is_returned", 0.0, 0.30)),
                warn(check_freshness(df, "updated_at", datetime.now(timezone.utc), 6)),
            ])
            log.info("гейт staging:\n%s", report.to_text())
            raise_if_failed(report)
            return report.summary()

        loaded = load_staging(freshness)
        checked = quality_staging()
        loaded >> checked
        return checked

    @task_group(group_id="marts")
    def marts(quality: dict) -> None:
        def _run_sql_file(filename: str) -> None:
            sql_text, _ = apply_sql_file(str(SQL_DIR / filename))
            PostgresHook(postgres_conn_id=DWH_CONN).run(sql_text, autocommit=True)
            log.info("выполнен %s", filename)

        @task
        def build_daily_revenue(quality: dict) -> dict:
            log.info("витрины строятся после гейта: %s", quality)
            _run_sql_file("mart_daily_revenue.sql")
            return {"built": "mart.daily_revenue"}

        @task
        def build_sku_abc(quality: dict) -> dict:
            _run_sql_file("mart_sku_abc.sql")
            return {"built": "mart.sku_abc"}

        revenue = build_daily_revenue(quality)
        abc = build_sku_abc(quality)
        return {"revenue": revenue, "abc": abc}

    @task
    def quality_marts(ds: str | None = None) -> dict:
        """Витрины проверяются отдельно: пустая или вздутая витрина — тоже инцидент."""
        hook = PostgresHook(postgres_conn_id=DWH_CONN)
        sql = "SELECT * FROM mart.daily_revenue WHERE order_date >= %s::date - 14"
        df = hook.get_pandas_df(sql, parameters=[ds])

        report = run_checks([
            check_rowcount(df, 14),
            check_rowcount_delta(df, 14, 3_000),
            check_not_null(df, ["order_date", "revenue", "orders"]),
            check_unique(df, ["order_date"]),
            check_positive(df, "revenue"),
        ])
        log.info("гейт витрин:\n%s", report.to_text())
        raise_if_failed(report)
        return report.summary()

    @task
    def publish_for_bi() -> None:
        """Обновляет материализованное представление, на котором сидят дашборды."""
        hook = PostgresHook(postgres_conn_id=DWH_CONN)
        refresh_sql, _ = refresh_matview_sql("bi", "revenue_daily", concurrently=True)
        hook.run(refresh_sql, autocommit=True)
        analyze_sql_text, _ = analyze_sql("mart", "daily_revenue")
        hook.run(analyze_sql_text, autocommit=True)
        log.info("bi.revenue_daily обновлено")

    done = EmptyOperator(task_id="done")

    freshness = check_source_freshness()
    staged = staging(freshness)
    built = marts(staged)
    checked = quality_marts()
    published = publish_for_bi()

    built >> checked >> published >> done


marketplace_daily_revenue()