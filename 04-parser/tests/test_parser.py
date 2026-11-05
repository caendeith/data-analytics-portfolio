"""Тесты парсера, нормализации и проверок качества.

Полностью офлайн: работают на фикстурах из tests/fixtures, сеть не нужна.
    python tests/test_parser.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from src.cleaner import (  # noqa: E402
    COLUMNS,
    normalize,
    summary,
    validate,
    write_csv,
    write_xlsx,
)
from src.fetcher import BASE_URL, Fetcher, absolute_url, cache_name  # noqa: E402
from src.parser import (  # noqa: E402
    parse_detail_page,
    parse_index_page,
    parse_price,
    parse_rating,
    parse_stock,
)

FIXTURES = ROOT / "tests" / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ============================================================== parser: каталог

def test_каталог_отдаёт_20_карточек():
    cards = parse_index_page(_fixture("catalog_page_1.html"))
    assert len(cards) == 20, f"ожидали 20 карточек, получили {len(cards)}"
    first = cards[0]
    assert first["title"] == "A Light in the Attic"
    assert first["price_text"] == "£51.77"
    assert first["rating_word"] == "Three"
    assert first["relative_url"].endswith("index.html")


def test_каталог_у_всех_карточек_есть_название_и_ссылка():
    cards = parse_index_page(_fixture("catalog_page_1.html"))
    for card in cards:
        assert card["title"], f"пустое название: {card}"
        assert card["relative_url"], f"пустая ссылка: {card}"
        assert parse_price(card["price_text"]) is not None


def test_пустой_html_не_роняет_парсер():
    assert parse_index_page("<html><body>ничего</body></html>") == []


# ============================================================== parser: карточка

def test_карточка_совпадает_с_эталоном():
    for n in (1, 2):
        html = _fixture(f"detail_{n}.html")
        expected = json.loads((FIXTURES / f"detail_{n}.expected.json").read_text(encoding="utf-8"))
        got = parse_detail_page(html)
        for key, value in expected.items():
            assert got[key] == value, f"detail_{n}.{key}: {got[key]!r} != {value!r}"


def test_карточка_достаёт_описание_и_категорию():
    got = parse_detail_page(_fixture("detail_1.html"))
    assert got["category"] == "Poetry"
    assert len(got["description"]) > 100
    assert got["image_url"], "не нашли картинку"


def test_карточка_без_таблицы_не_падает():
    got = parse_detail_page("<html><body><h1>пусто</h1></body></html>")
    assert got["upc"] == ""
    assert got["category"] == ""


# ============================================================== parser: скаляры

def test_price_разбирает_фунты():
    assert parse_price("£51.77") == 51.77
    assert parse_price("£0.00") == 0.0
    assert parse_price("") is None
    assert parse_price("нет цены") is None


def test_stock_разбирает_остаток():
    assert parse_stock("In stock (22 available)") == 22
    assert parse_stock("In stock (1 available)") == 1
    assert parse_stock("Out of stock") is None


def test_rating_переводит_слова_и_не_подставляет_ноль():
    assert parse_rating("One") == 1
    assert parse_rating("Five") == 5
    assert parse_rating("Three") == 3
    assert parse_rating("") is None
    assert parse_rating("Что-то") is None  # None, а не 0: ноль испортит средние


# ============================================================== fetcher

def test_cache_name_детерминирован_и_короток():
    a = cache_name("https://example.com/x")
    b = cache_name("https://example.com/x")
    c = cache_name("https://example.com/y")
    assert a == b and a != c
    assert len(a) == 21 and a.endswith(".html")


def test_absolute_url_разрешает_относительно_страницы():
    # первая страница каталога живёт в корне и отдаёт ссылки с префиксом catalogue/
    root = BASE_URL + "index.html"
    cat = BASE_URL + "catalogue/page-2.html"

    assert (absolute_url("catalogue/book_1/index.html", root)
            == BASE_URL + "catalogue/book_1/index.html")
    # страницы 2+ живут в /catalogue/ и отдают ссылки без префикса
    assert (absolute_url("book_1/index.html", cat)
            == BASE_URL + "catalogue/book_1/index.html")
    # картинки приходят с ../../
    assert absolute_url("../../media/cache/a.jpg", cat) == BASE_URL + "media/cache/a.jpg"
    # абсолютные ссылки не трогаем
    assert absolute_url("https://other.example/x", cat) == "https://other.example/x"
    assert absolute_url("", root) == BASE_URL


def test_fetcher_читает_из_кеша_и_не_ходит_в_сеть():
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        url = BASE_URL + "index.html"
        (cache / cache_name(url)).write_text("<html><body>ok</body></html>", encoding="utf-8")

        fetcher = Fetcher(cache_dir=cache, backend="requests")
        assert fetcher.has(url) is True
        assert "ok" in fetcher.read(url)

        fetcher.ensure([url])  # всё в кеше — сеть не должна дёрнуться
        assert fetcher.stats["network"] == 0


def test_fetcher_неизвестный_бэкенд_падает():
    try:
        Fetcher(backend="selenium")
    except ValueError:
        return
    raise AssertionError("ожидали ValueError на неизвестном backend")


# ============================================================== cleaner

def _row(**kw) -> dict:
    base = {
        "title": "Книга",
        "url": "https://books.toscrape.com/catalogue/x/index.html",
        "upc": "u1",
        "category": "Poetry",
        "price_incl_tax_text": "£10.00",
        "tax_text": "£0.00",
        "price_excl_tax_text": "£10.00",
        "rating_word": "Four",
        "availability_text": "In stock (5 available)",
        "reviews_text": "0",
        "description": "описание",
    }
    base.update(kw)
    return base


def test_normalize_приводит_типы():
    df = normalize([_row()])
    assert list(df.columns) == COLUMNS
    assert df.loc[0, "price_gbp"] == 10.0
    assert df.loc[0, "rating"] == 4
    assert df.loc[0, "stock"] == 5
    assert bool(df.loc[0, "in_stock"]) is True
    assert df.loc[0, "description_len"] == len("описание")


def test_normalize_убирает_дубли_по_upc():
    df = normalize([_row(upc="same", title="A"), _row(upc="same", title="B")])
    assert len(df) == 1
    assert df.loc[0, "title"] == "A", "должна остаться первая запись"


def test_normalize_не_подставляет_нули_вместо_неизвестного():
    df = normalize([_row(price_incl_tax_text="", rating_word="", availability_text="")])
    assert pd.isna(df.loc[0, "price_gbp"])
    assert pd.isna(df.loc[0, "rating"])
    assert pd.isna(df.loc[0, "stock"])


def test_validate_пропускает_хорошие_данные():
    report = validate(normalize([_row(upc=f"u{i}") for i in range(5)]))
    assert report.ok is True, report.to_text()
    assert report.rows == 5
    assert "провалов: 0" in report.to_text()


def test_validate_ловит_дубли_пустые_цены_и_битый_рейтинг():
    # normalize сам убирает дубли по UPC, поэтому проверки ниже скармливаются
    # напрямую: тестируем validate в изоляции, а не normalize заодно.
    df = normalize([
        _row(upc="u1"),
        _row(upc="u2"),
        _row(upc="u3", rating_word="Что-то"),
    ])
    df.loc[2, "upc"] = "u1"        # дубль
    df.loc[1, "price_gbp"] = None  # цена не разобралась

    report = validate(df)
    names = {c.name for c in report.failed}
    assert "UPC уникален" in names, names
    assert "цена разобрана" in names, names
    assert "рейтинг — целое 1..5" in names, names
    assert report.ok is False


def test_validate_ловит_пустые_категории_и_отрицательный_остаток():
    df = normalize([_row(upc="a", category=""), _row(upc="b")])
    df.loc[1, "stock"] = -3
    report = validate(df)
    names = {c.name for c in report.failed}
    assert "категория заполнена" in names
    assert "остаток не отрицательный" in names


def test_summary_агрегирует_по_категориям():
    df = normalize([
        _row(upc="a", category="A", price_incl_tax_text="£10", rating_word="Five"),
        _row(upc="b", category="A", price_incl_tax_text="£20", rating_word="Three"),
        _row(upc="c", category="B", price_incl_tax_text="£30", rating_word="One"),
    ])
    agg = summary(df)
    assert len(agg) == 2
    assert list(agg.columns)[0] == "category"
    row_a = agg[agg["category"] == "A"].iloc[0]
    assert row_a["книг"] == 2
    assert row_a["средняя_цена"] == 15.0
    assert row_a["средний_рейтинг"] == 4.0


def test_write_csv_и_xlsx_дают_читаемые_файлы():
    df = normalize([_row(upc=f"u{i}", category="A" if i % 2 else "B") for i in range(6)])
    report = validate(df)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        csv_path = tmp / "books.csv"
        xlsx_path = tmp / "books.xlsx"

        write_csv(df, csv_path)
        assert csv_path.exists() and csv_path.stat().st_size > 0
        back = pd.read_csv(csv_path)
        assert len(back) == 6
        assert {"upc", "price_gbp", "rating"} <= set(back.columns)

        write_xlsx(df, report, xlsx_path)
        assert xlsx_path.exists()
        wb = load_workbook(xlsx_path)
        assert wb.sheetnames == ["Данные", "Сводка"]
        ws = wb["Данные"]
        assert ws["A1"].value == "upc"
        assert ws.max_row == 7  # заголовок + 6 строк
        assert ws.freeze_panes == "A2"
        assert wb["Сводка"]["A1"].value == "Источник"


# ============================================================== runner

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