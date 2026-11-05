"""Вырезает из кеша фикстуры для офлайн-тестов парсера.

Запускается один раз при подготовке артефакта:
    python tests/make_fixtures.py
После этого тесты не ходят в сеть вообще.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.fetcher import BASE_URL, cache_name  # noqa: E402

CACHE = ROOT / "data" / "cache"
FIXTURES = ROOT / "tests" / "fixtures"


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)

    catalog_src = CACHE / cache_name(BASE_URL + "index.html")
    if not catalog_src.exists():
        print("нет кеша каталога — сначала запустите python main.py")
        return 1
    shutil.copyfile(catalog_src, FIXTURES / "catalog_page_1.html")

    raw = ROOT / "data" / "raw.jsonl"
    if not raw.exists():
        print("нет data/raw.jsonl — сначала запустите python main.py")
        return 1

    records = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines() if line.strip()]
    picked = 0
    for record in records:
        src = CACHE / cache_name(record["url"])
        if not src.exists():
            continue
        name = f"detail_{picked + 1}.html"
        shutil.copyfile(src, FIXTURES / name)
        print(f"{name:<20} <- {record['url']}")
        (FIXTURES / f"detail_{picked + 1}.expected.json").write_text(
            json.dumps({
                "upc": record.get("upc"),
                "title": record.get("title"),
                "category": record.get("category"),
                "price_incl_tax_text": record.get("price_incl_tax_text"),
                "availability_text": record.get("availability_text"),
                "rating_word": record.get("rating_word"),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        picked += 1
        if picked >= 2:
            break

    size = sum(p.stat().st_size for p in FIXTURES.glob("*.html"))
    print(f"\nфикстур: {picked + 1} html, {size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())