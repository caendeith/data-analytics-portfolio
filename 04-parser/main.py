"""Сборка датасета с books.toscrape.com: каталог -> карточки -> чистая таблица.

    python main.py                              # 5 страниц каталога (100 книг)
    python main.py --max-pages 50               # весь каталог (1000 книг)
    python main.py --backend browser            # если HTTP режется прокси
    python main.py --max-pages 5 --delay 0.5

Почему этот сайт: books.toscrape.com — публичный полигон, специально сделанный
для отработки скрейпинга. Показывать в портфолио парсер чужого маркетплейса
не стоит: это нарушение ToS и повод для блокировки, а не пример хорошей работы.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.cleaner import normalize, validate, write_csv, write_report, write_xlsx  # noqa: E402
from src.fetcher import BASE_URL, Fetcher, FetchError, absolute_url  # noqa: E402
from src.parser import parse_detail_page, parse_index_page  # noqa: E402

log = logging.getLogger("parser")


def catalog_url(page: int) -> str:
    """Первая страница лежит в корне, остальные — в catalogue/page-N.html."""
    if page <= 1:
        return BASE_URL + "index.html"
    return f"{BASE_URL}catalogue/page-{page}.html"


def load_checkpoint(path: Path) -> dict[str, dict]:
    """Читает уже собранные карточки: прерванный запуск продолжается, а не начинается заново."""
    if not path.exists():
        return {}
    done: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # обрыв записи от прерванного запуска — просто пропускаем
            if record.get("url"):
                done[record["url"]] = record
    return done


def run(max_pages: int, delay: float, backend: str, data_dir: Path, out_dir: Path) -> int:
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = data_dir / "raw.jsonl"

    fetcher = Fetcher(delay=delay, cache_dir=data_dir / "cache", backend=backend)

    # ---------------------------------------------------------------- этап 1: каталог
    catalog_urls = [catalog_url(p) for p in range(1, max_pages + 1)]
    fetcher.ensure(catalog_urls)

    items: list[dict] = []
    for page, url in enumerate(catalog_urls, start=1):
        try:
            html = fetcher.read(url)
        except FetchError as exc:
            log.error("каталог, страница %s недоступна: %s", page, exc)
            break
        cards = parse_index_page(html)
        if not cards:
            log.info("страница %s пустая — каталог закончился", page)
            break
        for card in cards:
            card["url"] = absolute_url(card["relative_url"], url)
        items.extend(cards)
        log.info("каталог: страница %s -> %s книг (всего %s)", page, len(cards), len(items))

    if not items:
        log.error("не удалось собрать ни одной карточки — дальше идти некуда")
        return 1

    # ---------------------------------------------------------------- этап 2: карточки
    done = load_checkpoint(raw_path)
    if done:
        log.info("найден чекпоинт: %s записей, они будут переиспользованы", len(done))

    product_urls = [item["url"] for item in items]
    fetcher.ensure(product_urls)

    rows: list[dict] = []
    fresh = 0
    failed = 0
    with raw_path.open("a", encoding="utf-8") as sink:
        for i, item in enumerate(items, start=1):
            if item["url"] in done:
                rows.append(done[item["url"]])
                continue
            try:
                detail = parse_detail_page(fetcher.read(item["url"]))
            except FetchError as exc:
                failed += 1
                log.warning("пропуск %s: %s", item["url"], exc)
                continue
            record = {**item, **detail, "url": item["url"]}
            sink.write(json.dumps(record, ensure_ascii=False) + "\n")
            sink.flush()
            rows.append(record)
            fresh += 1
            if fresh % 25 == 0:
                log.info("карточки: %s/%s", i, len(items))

    log.info("данные: %s записей (%s новых, %s из чекпоинта, %s не удалось)",
             len(rows), fresh, len(rows) - fresh, failed)
    log.info("сеть: %s, кеш: %s, браузер: %s, ретраев: %s",
             fetcher.stats["network"], fetcher.stats["hits"],
             fetcher.stats["browser"], fetcher.stats["retries"])

    # ---------------------------------------------------------------- этап 3: чистая таблица
    df = normalize(rows)
    report = validate(df)

    write_csv(df, data_dir / "books_clean.csv")
    write_report(report, data_dir / "quality_report.json")
    write_xlsx(df, report, out_dir / "books.xlsx")

    print()
    print(report.to_text())
    print()
    print(f"строк в чистой таблице : {len(df)}")
    print(f"категорий              : {df['category'].nunique()}")
    print(f"средняя цена           : £{df['price_gbp'].mean():.2f}")
    print(f"файлы                  : {data_dir / 'books_clean.csv'}")
    print(f"                         {out_dir / 'books.xlsx'}")
    print(f"                         {data_dir / 'quality_report.json'}")

    if not report.ok:
        print("\nВНИМАНИЕ: не все проверки пройдены, файл не стоит отдавать как финальный")
        return 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Парсер каталога books.toscrape.com")
    ap.add_argument("--max-pages", type=int, default=5, help="сколько страниц каталога обойти")
    ap.add_argument("--delay", type=float, default=0.25, help="пауза между запросами, сек")
    ap.add_argument("--backend", choices=["requests", "browser", "auto"], default="auto",
                    help="транспорт загрузки")
    ap.add_argument("--data", type=Path, default=ROOT / "data", help="куда писать сырые данные")
    ap.add_argument("--out", type=Path, default=ROOT / "out", help="куда писать итоговые файлы")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.max_pages < 1:
        ap.error("--max-pages должен быть >= 1")
    if args.delay < 0:
        ap.error("--delay не может быть отрицательным")

    return run(args.max_pages, args.delay, args.backend, args.data, args.out)


if __name__ == "__main__":
    sys.exit(main())