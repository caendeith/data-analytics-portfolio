"""Разбор HTML: чистые функции без сети и без побочных эффектов.

Каждая функция принимает текст страницы и возвращает структуру — поэтому
парсер тестируется на сохранённых фикстурах, без единого запроса.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}

_STOCK_RE = re.compile(r"(\d+)")
_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")


def parse_index_page(html: str) -> list[dict[str, Any]]:
    """Карточки со страницы каталога: то, что видно без захода в товар."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []

    for card in soup.select("article.product_pod"):
        link = card.select_one("h3 a")
        if link is None:
            continue

        rating_el = card.select_one("p.star-rating")
        rating_word = ""
        if rating_el is not None:
            classes = [c for c in rating_el.get("class", []) if c != "star-rating"]
            rating_word = classes[0] if classes else ""

        out.append({
            "title": (link.get("title") or link.get_text(strip=True)).strip(),
            "relative_url": (link.get("href") or "").strip(),
            "price_text": _text(card.select_one("p.price_color")),
            "rating_word": rating_word,
            "availability_text": _text(card.select_one("p.instock.availability")),
        })

    return out


def parse_detail_page(html: str) -> dict[str, Any]:
    """Карточка товара: таблица характеристик, категория, описание."""
    soup = BeautifulSoup(html, "html.parser")

    # breadcrumb: Home > Books > <Category> > <Title>
    crumbs = [a.get_text(strip=True) for a in soup.select("ul.breadcrumb li a")]
    category = crumbs[2] if len(crumbs) >= 3 else ""

    table: dict[str, str] = {}
    for row in soup.select("table.table.table-striped tr"):
        th, td = row.select_one("th"), row.select_one("td")
        if th is not None and td is not None:
            table[th.get_text(strip=True)] = td.get_text(strip=True)

    description_el = soup.select_one("#product_description ~ p")
    title_el = soup.select_one(".product_main h1")
    rating_el = soup.select_one("p.star-rating")
    rating_word = ""
    if rating_el is not None:
        classes = [c for c in rating_el.get("class", []) if c != "star-rating"]
        rating_word = classes[0] if classes else ""

    image = soup.select_one("#product_gallery img")

    return {
        "title": (title_el.get_text(strip=True) if title_el else ""),
        "upc": table.get("UPC", ""),
        "product_type": table.get("Product Type", ""),
        "price_excl_tax_text": table.get("Price (excl. tax)", ""),
        "price_incl_tax_text": table.get("Price (incl. tax)", ""),
        "tax_text": table.get("Tax", ""),
        "availability_text": table.get("Availability", ""),
        "reviews_text": table.get("Number of reviews", ""),
        "category": category,
        "rating_word": rating_word or "",
        "description": (description_el.get_text(strip=True) if description_el else ""),
        "image_url": (image.get("src") or "") if image is not None else "",
    }


def parse_price(text: str) -> float | None:
    """'£51.77' -> 51.77. Валюта отбрасывается, десятичный разделитель — точка."""
    if not text:
        return None
    match = _NUMBER_RE.search(text.replace(",", ""))
    return float(match.group(1)) if match else None


def parse_stock(text: str) -> int | None:
    """'In stock (22 available)' -> 22."""
    if not text:
        return None
    match = _STOCK_RE.search(text)
    return int(match.group(1)) if match else None


def parse_rating(word: str) -> int | None:
    """'Three' -> 3. Неизвестное слово -> None, а не 0: ноль исказит средние."""
    return RATING_WORDS.get((word or "").strip())


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el is not None else ""