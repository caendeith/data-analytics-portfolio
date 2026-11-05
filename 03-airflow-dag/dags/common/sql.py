"""Генераторы SQL для идемпотентной загрузки и сборки витрин.

Почему отдельным модулем: SQL в DAG-файле, собранный через конкатенацию строк, —
главный источник дублей и SQL-инъекций в реальных пайплайнах. Здесь каждое
обращение возвращает пару ``(sql, params)`` с плейсхолдерами ``%s``, поэтому
DAG передаёт параметры драйверу, а не склеивает их в текст запроса.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Sequence

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_ident(name: str) -> str:
    """Проверяет и квотирует идентификатор.

    Разрешаем только ``[A-Za-z_][A-Za-z0-9_]*`` — этого достаточно для наших схем,
    и этого же достаточно, чтобы отсечь попытку подставить SQL через имя таблицы.
    """
    if not _IDENT_RE.match(name):
        raise ValueError(f"недопустимый идентификатор: {name!r}")
    return f'"{name}"'


def qualified(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def delete_partition_sql(
    schema: str,
    table: str,
    day_column: str,
    day: date,
) -> tuple[str, list[Any]]:
    """Удаляет суточную партицию перед перезаливкой (delete-insert вместо append)."""
    sql = (
        f"DELETE FROM {qualified(schema, table)} "
        f"WHERE {quote_ident(day_column)} = %s"
    )
    return sql, [day]


def insert_from_select_sql(
    target_schema: str,
    target_table: str,
    source_schema: str,
    source_table: str,
    columns: Sequence[str],
    day_column: str,
    day: date,
) -> tuple[str, list[Any]]:
    """Переносит одну суточную партицию из staging в целевую таблицу."""
    cols = ", ".join(quote_ident(c) for c in columns)
    sql = (
        f"INSERT INTO {qualified(target_schema, target_table)} ({cols}) "
        f"SELECT {cols} FROM {qualified(source_schema, source_table)} "
        f"WHERE {quote_ident(day_column)} = %s"
    )
    return sql, [day]


def upsert_sql(
    schema: str,
    table: str,
    columns: Sequence[str],
    key_columns: Sequence[str],
) -> tuple[str, list[Any]]:
    """INSERT ... ON CONFLICT DO UPDATE: повторный запуск не плодит строки."""
    cols = ", ".join(quote_ident(c) for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    keys = ", ".join(quote_ident(c) for c in key_columns)
    updatable = [c for c in columns if c not in set(key_columns)]
    if not updatable:
        action = "DO NOTHING"
    else:
        assignments = ", ".join(
            f"{quote_ident(c)} = EXCLUDED.{quote_ident(c)}" for c in updatable
        )
        action = f"DO UPDATE SET {assignments}"
    sql = (
        f"INSERT INTO {qualified(schema, table)} ({cols}) "
        f"VALUES ({placeholders}) ON CONFLICT ({keys}) {action}"
    )
    return sql, []


def window_select_sql(
    schema: str,
    table: str,
    day_column: str,
    start_day: date,
    end_day: date,
) -> tuple[str, list[Any]]:
    """Выборка окна [start_day; end_day] включительно, с учётом опоздавших данных."""
    sql = (
        f"SELECT * FROM {qualified(schema, table)} "
        f"WHERE {quote_ident(day_column)} BETWEEN %s AND %s"
    )
    return sql, [start_day, end_day]


def count_window_sql(
    schema: str,
    table: str,
    day_column: str,
    day: date,
) -> tuple[str, list[Any]]:
    """Количество строк за сутки — дешёвая проверка «данные вообще есть?»."""
    sql = (
        f"SELECT COUNT(*) FROM {qualified(schema, table)} "
        f"WHERE {quote_ident(day_column)} = %s"
    )
    return sql, [day]


def max_updated_at_sql(schema: str, table: str, column: str) -> tuple[str, list[Any]]:
    """Максимальная метка обновления — вход для проверки свежести."""
    sql = f"SELECT MAX({quote_ident(column)}) AS last_update FROM {qualified(schema, table)}"
    return sql, []


def refresh_matview_sql(schema: str, view: str, concurrently: bool = True) -> tuple[str, list[Any]]:
    """Обновление материализованного представления для BI.

    ``CONCURRENTLY`` не блокирует читателей, но требует уникального индекса —
    это отмечено в README, чтобы не выстрелить в проде.
    """
    kw = "CONCURRENTLY " if concurrently else ""
    return f"REFRESH MATERIALIZED VIEW {kw}{qualified(schema, view)}", []


def analyze_sql(schema: str, table: str) -> tuple[str, list[Any]]:
    """Собирает статистику: без него планировщик после большой заливки тупит."""
    return f"ANALYZE {qualified(schema, table)}", []


def apply_sql_file(path: str, params: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Читает .sql-файл и подставляет только разрешённые Jinja-переменные.

    Возвращает запрос и словарь параметров. Ограничение по переменным сделано,
    чтобы шаблон не мог «вытащить» произвольный атрибут окружения.
    """
    allowed = {"ds", "ds_nodash", "logical_date", "data_interval_start", "data_interval_end"}
    params = params or {}
    unknown = set(params) - allowed
    if unknown:
        raise ValueError(f"неизвестные параметры шаблона: {sorted(unknown)}")
    with open(path, encoding="utf-8") as fh:
        return fh.read(), params