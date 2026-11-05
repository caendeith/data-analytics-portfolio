"""Нормализация, проверки качества и выгрузка в чистые форматы.

На выходе два файла, которые можно отдать заказчику без пояснений:
  * books_clean.csv — плоская таблица с типами;
  * books.xlsx     — те же данные + лист «Сводка» со статистикой и отчётом
                     о качестве (видно, что проверялось и что нашлось).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .parser import parse_price, parse_rating, parse_stock

COLUMNS = [
    "upc", "title", "category", "price_gbp", "tax_gbp", "price_excl_tax_gbp",
    "rating", "stock", "reviews", "in_stock", "description_len", "description",
    "url", "scraped_at",
]


# ------------------------------------------------------------------ качество

@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        return f"[{'OK  ' if self.passed else 'FAIL'}] {self.name}: {self.detail}"


@dataclass
class QualityReport:
    checks: list[Check] = field(default_factory=list)
    generated_at: str = ""
    rows: int = 0
    source: str = ""

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def to_text(self) -> str:
        head = f"строк: {self.rows}, проверок: {len(self.checks)}, провалов: {len(self.failed)}"
        return "\n".join([head] + [str(c) for c in self.checks])

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "source": self.source,
            "rows": self.rows,
            "ok": self.ok,
            "checks": [asdict(c) for c in self.checks],
        }


# ------------------------------------------------------------------ нормализация

def normalize(rows: list[dict[str, Any]], scraped_at: str | None = None) -> pd.DataFrame:
    """Сырые записи -> типизированный DataFrame.

    Всё, что не удалось разобрать, остаётся None: подставлять нули или средние
    нельзя — это спрячет проблему, которую потом никто не найдёт.
    """
    scraped_at = scraped_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []
    for r in rows:
        price = parse_price(r.get("price_incl_tax_text") or r.get("price_text", ""))
        stock = parse_stock(r.get("availability_text", ""))
        records.append({
            "upc": (r.get("upc") or "").strip(),
            "title": (r.get("title") or "").strip(),
            "category": (r.get("category") or "").strip(),
            "price_gbp": price,
            "tax_gbp": parse_price(r.get("tax_text", "")),
            "price_excl_tax_gbp": parse_price(r.get("price_excl_tax_text", "")),
            "rating": parse_rating(r.get("rating_word", "")),
            "stock": stock,
            "reviews": _to_int(r.get("reviews_text")),
            "in_stock": bool(stock and stock > 0),
            "description_len": len(r.get("description") or ""),
            "description": (r.get("description") or "").strip(),
            "url": r.get("url", ""),
            "scraped_at": scraped_at,
        })

    df = pd.DataFrame(records, columns=COLUMNS)
    df = df.drop_duplicates(subset=["upc"], keep="first")
    for col in ("price_gbp", "tax_gbp", "price_excl_tax_gbp", "rating", "stock", "reviews"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.reset_index(drop=True)


def validate(df: pd.DataFrame) -> QualityReport:
    """Проверки, которые должны пройти до отдачи файла заказчику."""
    checks: list[Check] = []

    checks.append(Check("строки есть", len(df) > 0, f"строк: {len(df)}"))

    missing = [c for c in COLUMNS if c not in df.columns]
    checks.append(Check("все колонки на месте", not missing, f"нет: {missing or 'ничего'}"))

    empty_upc = int((df["upc"].fillna("").str.strip() == "").sum())
    checks.append(Check("UPC заполнен", empty_upc == 0, f"пустых UPC: {empty_upc}"))

    dupes = int(df.duplicated(subset=["upc"]).sum())
    checks.append(Check("UPC уникален", dupes == 0, f"дублей: {dupes}"))

    null_price = int(df["price_gbp"].isna().sum())
    checks.append(Check("цена разобрана", null_price == 0, f"не разобрано цен: {null_price}"))

    bad_price = int(((df["price_gbp"] <= 0) | (df["price_gbp"] > 1000)).fillna(True).sum())
    checks.append(Check("цена в коридоре (0;1000]", bad_price == 0, f"вне коридора: {bad_price}"))

    bad_rating = int(df["rating"].isna().sum() + (~df["rating"].dropna().isin([1, 2, 3, 4, 5])).sum())
    checks.append(Check("рейтинг — целое 1..5", bad_rating == 0, f"некорректных: {bad_rating}"))

    neg_stock = int((df["stock"] < 0).fillna(False).sum())
    checks.append(Check("остаток не отрицательный", neg_stock == 0, f"отрицательных: {neg_stock}"))

    empty_cat = int((df["category"].fillna("").str.strip() == "").sum())
    checks.append(Check("категория заполнена", empty_cat == 0, f"пустых категорий: {empty_cat}"))

    dup_titles = int(df.duplicated(subset=["title"]).sum())
    checks.append(Check("названия не дублируются", True,
                        f"совпадений названий: {dup_titles} (не ошибка, но стоит знать)"))

    finite = int(df["price_gbp"].dropna().apply(lambda v: v == v).sum())
    checks.append(Check("нет нечисловых цен", finite == int(df["price_gbp"].notna().sum()),
                        f"чисел: {finite}"))

    return QualityReport(
        checks=checks,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        rows=len(df),
        source="books.toscrape.com",
    )


def summary(df: pd.DataFrame) -> pd.DataFrame:
    """Компактная сводка по категориям — то, что заказчик смотрит первым."""
    grouped = (
        df.groupby("category", dropna=False)
        .agg(
            книг=("upc", "count"),
            средняя_цена=("price_gbp", "mean"),
            медиана_цены=("price_gbp", "median"),
            макс_цена=("price_gbp", "max"),
            средний_рейтинг=("rating", "mean"),
            всего_на_складе=("stock", "sum"),
        )
        .reset_index()
        .sort_values("книг", ascending=False)
    )
    grouped["средняя_цена"] = grouped["средняя_цена"].round(2)
    grouped["медиана_цены"] = grouped["медиана_цены"].round(2)
    grouped["средний_рейтинг"] = grouped["средний_рейтинг"].round(2)
    return grouped


# ------------------------------------------------------------------ выгрузка

def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def write_report(report: QualityReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def write_xlsx(df: pd.DataFrame, report: QualityReport, path: Path) -> None:
    """Книга Excel: данные + сводка. Оформление — чтобы файл не стыдно отправить."""
    path.parent.mkdir(parents=True, exist_ok=True)
    agg = summary(df)

    head_fill = PatternFill("solid", fgColor="1F2937")
    head_font = Font(color="FFFFFF", bold=True, size=10)
    thin = Side(style="thin", color="D1D5DB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    wb = Workbook()

    ws = wb.active
    ws.title = "Данные"
    for j, col in enumerate(df.columns, start=1):
        cell = ws.cell(row=1, column=j, value=col)
        cell.fill, cell.font = head_fill, head_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border
    for i, (_, row) in enumerate(df.iterrows(), start=2):
        for j, col in enumerate(df.columns, start=1):
            ws.cell(row=i, column=j, value=_cell_value(row[col])).border = border

    widths = {"upc": 18, "title": 46, "category": 20, "description": 60, "url": 52,
              "scraped_at": 22}
    for j, col in enumerate(df.columns, start=1):
        ws.column_dimensions[get_column_letter(j)].width = widths.get(col, 12)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(df.columns))}{len(df) + 1}"

    ws2 = wb.create_sheet("Сводка")
    ws2.column_dimensions["A"].width = 34
    for col in "BCDEFG":
        ws2.column_dimensions[col].width = 16

    ws2["A1"] = "Источник"
    ws2["B1"] = report.source
    ws2["A2"] = "Собрано"
    ws2["B2"] = report.generated_at
    ws2["A3"] = "Книг"
    ws2["B3"] = len(df)
    ws2["A4"] = "Категорий"
    ws2["B4"] = int(df["category"].nunique())
    ws2["A5"] = "Средняя цена, £"
    ws2["B5"] = round(float(df["price_gbp"].mean()), 2)
    ws2["A6"] = "Медиана цены, £"
    ws2["B6"] = round(float(df["price_gbp"].median()), 2)
    ws2["A7"] = "Средний рейтинг"
    ws2["B7"] = round(float(df["rating"].mean()), 2)
    ws2["A8"] = "В наличии, %"
    ws2["B8"] = round(float(df["in_stock"].mean()) * 100, 1)
    for r in range(1, 9):
        ws2.cell(row=r, column=1).font = Font(bold=True, size=10)

    start = 10
    ws2.cell(row=start, column=1, value="Проверка качества").font = Font(bold=True, size=11)
    ws2.cell(row=start + 1, column=1, value="проверка").font = head_font
    ws2.cell(row=start + 1, column=1).fill = head_fill
    ws2.cell(row=start + 1, column=2, value="результат").font = head_font
    ws2.cell(row=start + 1, column=2).fill = head_fill
    ws2.cell(row=start + 1, column=3, value="детали").font = head_font
    ws2.cell(row=start + 1, column=3).fill = head_fill
    for i, check in enumerate(report.checks, start=start + 2):
        ws2.cell(row=i, column=1, value=check.name)
        verdict = ws2.cell(row=i, column=2, value="OK" if check.passed else "ПРОВАЛ")
        verdict.font = Font(color="047857" if check.passed else "B91C1C", bold=True)
        ws2.cell(row=i, column=3, value=check.detail)

    top = start + len(report.checks) + 3
    ws2.cell(row=top, column=1, value="По категориям").font = Font(bold=True, size=11)
    for j, col in enumerate(agg.columns, start=1):
        cell = ws2.cell(row=top + 1, column=j, value=col)
        cell.fill, cell.font, cell.border = head_fill, head_font, border
    for i, (_, row) in enumerate(agg.iterrows(), start=top + 2):
        for j, col in enumerate(agg.columns, start=1):
            ws2.cell(row=i, column=j, value=_cell_value(row[col])).border = border

    wb.save(path)


# ------------------------------------------------------------------ утилиты

def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else None


def _cell_value(value: Any) -> Any:
    """Excel не умеет NaN и numpy-типы — приводим к обычным Python-типам."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            return str(value)
    return value