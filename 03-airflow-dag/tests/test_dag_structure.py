"""Статические проверки DAG-файла.

Airflow в этом окружении не установлен, поэтому DAG разбирается через AST:
проверяем, что в нём есть обязательные для продакшена вещи (расписание,
отключённый catchup, ретраи, SLA, колбэки ошибок, гейты качества), и что
связанные SQL-файлы существуют и согласованы с DAG.

Запуск:  python tests/test_dag_structure.py
Если Airflow всё же установлен, дополнительно прогоняется настоящий DagBag.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAG_FILE = ROOT / "dags" / "marketplace_etl.py"
SQL_DIR = ROOT / "dags" / "sql"
INIT_SQL = ROOT / "docker" / "init.sql"

SOURCE = DAG_FILE.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


# ------------------------------------------------------------------helpers

def _decorator_name(node: ast.AST) -> str | None:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _functions() -> list[ast.FunctionDef]:
    return [n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)]


def _dag_call() -> ast.Call:
    for fn in _functions():
        for dec in fn.decorator_list:
            if _decorator_name(dec) == "dag":
                if not isinstance(dec, ast.Call):
                    raise AssertionError("@dag должен вызываться с аргументами")
                return dec
    raise AssertionError("в файле не найден @dag")


def _kw(call: ast.Call) -> dict[str, ast.expr]:
    return {k.arg: k.value for k in call.keywords if k.arg}


def _const(node: ast.expr):
    try:
        return ast.literal_eval(node)
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"не удалось вычислить литерал: {ast.dump(node)[:80]}") from exc


def _module_dict(name: str) -> dict:
    for node in TREE.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    if isinstance(node.value, ast.Dict):
                        out = {}
                        for k, v in zip(node.value.keys, node.value.values):
                            if isinstance(k, ast.Constant):
                                out[k.value] = v
                        return out
    raise AssertionError(f"не найдено словарное присваивание {name}")


# ------------------------------------------------------------------tests

def test_dag_имеет_расписание_и_отключённый_catchup():
    kw = _kw(_dag_call())
    assert "schedule" in kw, "нет schedule — DAG не будет запускаться по времени"
    assert _const(kw["schedule"]) == "0 3 * * *"
    assert _const(kw["catchup"]) is False, "catchup=True завалит систему бэкфиллом"
    assert _const(kw["max_active_runs"]) == 1, "без ограничения параллельных запусков витрина задвоится"


def test_dag_имеет_стартовую_дату_и_теги():
    kw = _kw(_dag_call())
    assert isinstance(kw.get("start_date"), ast.Call), "start_date должен быть datetime(...)"
    tags = [_const(t) for t in kw["tags"].elts]
    assert "marketplace" in tags and "daily" in tags


def test_default_args_содержат_ретраи_таймаут_и_sla():
    da = _module_dict("default_args")
    for required in ("owner", "retries", "retry_delay", "execution_timeout", "sla"):
        assert required in da, f"в default_args нет {required}"
    assert _const(da["retries"]) >= 2, "меньше двух ретраев — пайплайн падает от случайного сбоя"
    backoff = da.get("retry_exponential_backoff")
    assert backoff is not None and _const(backoff) is True, "нужен экспоненциальный бэкофф"


def test_есть_колбэки_ошибки_и_нарушения_sla():
    kw = _kw(_dag_call())
    assert "on_failure_callback" in kw, "о падении никто не узнает"
    assert "sla_miss_callback" in kw
    names = {f.name for f in _functions()}
    assert "alert_on_failure" in names
    assert "alert_on_sla_miss" in names


def test_секреты_не_захардкожены():
    assert "Variable.get" in SOURCE, "адрес алертов должен браться из Variable"
    bad = re.findall(r"(?i)\b(password|passwd|secret|api_key|token)\s*=\s*['\"][^'\"]+['\"]", SOURCE)
    assert not bad, f"похоже на захардкоженный секрет: {bad}"


def test_задача_свежести_падает_а_не_пропускает():
    src = _function_source("check_source_freshness")
    assert "raise ValueError" in src, "устаревший источник должен ронять запуск"
    assert "is_fresh" in src


def test_staging_идемпотентен_через_delete_insert():
    src = _function_source("load_staging")
    assert "delete_partition_sql" in src, "без delete будет дублирование при повторном запуске"
    assert "insert_from_select_sql" in src
    delete_at = src.index("delete_partition_sql")
    insert_at = src.index("insert_from_select_sql")
    assert delete_at < insert_at, "delete обязан идти до insert"


def test_есть_гейты_качества_между_слоями():
    assert "raise_if_failed" in SOURCE
    names = {f.name for f in _functions()}
    assert {"quality_staging", "quality_marts"} <= names, "нужны гейты и на staging, и на витринах"
    # гейт должен стоять до построения витрин по цепочке зависимостей
    assert "marts(staged)" in SOURCE, "витрины должны зависеть от результата гейта staging"


def test_используются_task_group_и_task_flow():
    tasks = [fn for fn in _functions()
             if any(_decorator_name(d) in ("task", "task_group") for d in fn.decorator_list)]
    plain = [fn.name for fn in tasks
             if any(_decorator_name(d) == "task" for d in fn.decorator_list)]
    groups = [fn.name for fn in tasks
              if any(_decorator_name(d) == "task_group" for d in fn.decorator_list)]
    assert len(plain) >= 6, f"ожидали минимум 6 задач, нашли {len(plain)}: {plain}"
    assert {"staging", "marts"} <= set(groups), f"нет групп: {groups}"


def test_sql_файлы_существуют_и_согласованы():
    for fname in ("mart_daily_revenue.sql", "mart_sku_abc.sql"):
        path = SQL_DIR / fname
        assert path.exists(), f"нет файла {fname}"
        text = path.read_text(encoding="utf-8")
        assert "BEGIN;" in text and "COMMIT;" in text, f"{fname}: нет явной транзакции"
        assert "DELETE" in text, f"{fname}: нет удаления партиции — будут дубли"
    assert "mart_daily_revenue.sql" in SOURCE
    assert "mart_sku_abc.sql" in SOURCE


def test_matview_в_init_имеет_уникальный_индекс():
    """REFRESH ... CONCURRENTLY падает без уникального индекса — проверяем пару файлов."""
    init = INIT_SQL.read_text(encoding="utf-8")
    assert "revenue_daily" in init
    assert re.search(r"CREATE\s+UNIQUE\s+INDEX[^;]*revenue_daily", init), (
        "в docker/init.sql нет уникального индекса на bi.revenue_daily — "
        "REFRESH MATERIALIZED VIEW CONCURRENTLY из DAG упадёт"
    )


def test_dag_импортируется_если_airflow_установлен():
    try:
        from airflow.models import DagBag  # noqa: F401
    except Exception:
        print("      (пропуск: airflow не установлен, проверен только AST)")
        return
    from airflow.models import DagBag

    bag = DagBag(dag_folder=str(ROOT / "dags"), include_examples=False)
    assert not bag.import_errors, f"ошибки импорта: {bag.import_errors}"
    assert "marketplace_daily_revenue" in bag.dag_ids, f"найдены DAG'и: {bag.dag_ids}"
    dag = bag.get_dag("marketplace_daily_revenue")
    assert len(dag.tasks) >= 8, f"задач всего {len(dag.tasks)}"
    group_ids = {tg.group_id for tg in dag.task_group.children.values()
                 if hasattr(tg, "group_id")}
    assert {"staging", "marts"} <= group_ids, f"группы: {group_ids}"


def _function_source(name: str) -> str:
    for fn in _functions():
        if fn.name == name:
            return ast.get_source_segment(SOURCE, fn) or ""
    raise AssertionError(f"нет функции {name}")


# ------------------------------------------------------------------runner

def main() -> int:
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc))
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")

    print(f"\nитого: {len(tests) - len(failed)} из {len(tests)} прошли")
    for name, exc in failed:
        print(f"  - {name}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())