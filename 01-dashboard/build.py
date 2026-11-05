"""
Генератор данных и сборщик дашборда.

Запуск:  python build.py
Результат: dashboard.html (самодостаточный файл, работает офлайн),
           data/sales_daily.csv, data/skus.csv

Данные синтетические: структура повторяет реальный кейс аналитики
продавца на маркетплейсе, значения сгенерированы для демонстрации методологии.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

RNG = np.random.default_rng(20261021)
HERE = Path(__file__).parent
OUT = HERE / "dashboard.html"
DATA_DIR = HERE / "data"

DAYS = 180
END = date(2026, 11, 4)
START = END - timedelta(days=DAYS - 1)

# категория -> (доля выручки, средний чек, доля возвратов, доля отмен)
CATS: list[tuple[str, float, float, float, float]] = [
    ("Электроника",      0.27, 8200, 0.062, 0.031),
    ("Одежда",           0.21, 2450, 0.184, 0.052),
    ("Дом и сад",        0.15, 3100, 0.081, 0.034),
    ("Красота",          0.12, 1450, 0.048, 0.028),
    ("Детские товары",   0.09, 1980, 0.071, 0.030),
    ("Спорт",            0.07, 3600, 0.093, 0.041),
    ("Продукты",         0.05,  980, 0.026, 0.019),
    ("Аксессуары",       0.04, 1150, 0.112, 0.044),
]

SKU_NAMES = {
    "Электроника": ["Смартфон X6 128Gb", "Наушники TWS Air", "Робот-пылесос S3", "Ноутбук 14 Slim",
                    "Умная колонка Mini", "Powerbank 20000", "Телевизор 43 4K", "Клавиатура Mech RGB",
                    "Мышь беспроводная Pro", "Камера IP 2K", "Часы смарт Pro", "Зарядка GaN 65W"],
    "Одежда": ["Куртка зимняя пуховая", "Джинсы Slim синие", "Худи оверсайз беж", "Платье миди льняное",
               "Кроссовки беговые", "Футболка базовая хлопок", "Свитер шерстяной", "Брюки карго хаки",
               "Рубашка оверсайз", "Термобелье комплект", "Плащ демисезон", "Носки 10 пар"],
    "Дом и сад": ["Органайзер кухонный", "Набор кастрюль 5 шт", "Лампа настольная LED", "Коврик придверный",
                  "Стеллаж 5 полок", "Подушка анатомическая", "Плед флисовый", "Сушилка для белья",
                  "Корзина для хранения", "Нож кухонный шеф", "Ваза керамическая", "Гирлянда 20м"],
    "Красота": ["Сыворотка витамин C", "Крем увлажняющий 50мл", "Тушь объёмная", "Шампунь проф 400мл",
                "Палетка теней 12", "Бальзам для губ", "Маска для лица 5 шт", "Парфюм цветочный 30мл",
                "Лак для ногтей", "Мист для лица", "Скраб кофейный", "Помада матовая"],
    "Детские товары": ["Конструктор 500 деталей", "Мягкая игрушка заяц", "Развивающий коврик", "Пазл 1000",
                       "Рюкзак школьный", "Настольная игра", "Коляска-трость", "Смесь детская 800г",
                       "Книга-панорама", "Кукла интерактивная", "Пластилин 12 цветов", "Санки-ледянки"],
    "Спорт": ["Коврик для йоги", "Гантели 2х5кг", "Скакалка скоростная", "Бутылка спортивная 1л",
              "Эспандер набор", "Рюкзак трекинговый", "Палатка 2-местная", "Велотренажёр компактный",
              "Массажный ролл", "Термос 0.75", "Перчатки для зала", "Резинки фитнес набор"],
    "Продукты": ["Кофе в зёрнах 1кг", "Протеин 900г", "Орехи микс 500г", "Чай зелёный 100 пак",
                 "Оливковое масло 1л", "Мёд натуральный 500г", "Шоколад горький 70%", "Крупа киноа 500г",
                 "Сушёные ягоды 200г", "Специи набор 6 шт", "Сироп топинамбура", "Паста урбеч 300г"],
    "Аксессуары": ["Сумка шоппер холст", "Ремень кожаный", "Очки солнцезащитные", "Шарф пашмина",
                   "Кошелёк RFID", "Чехол для карт", "Браслет кожаный", "Зонт автомат",
                   "Перчатки замш", "Платок шёлковый", "Клатч вечерний", "Брелок кожаный"],
}

WEEKDAY_K = np.array([1.02, 1.00, 1.01, 1.05, 1.12, 0.94, 0.86])  # Пн..Вс
HOUR_K = np.array([
    .18, .10, .07, .06, .06, .08, .16, .30, .52, .70, .82, .92,
    .98, .86, .80, .84, .92, 1.02, 1.16, 1.32, 1.38, 1.18, .78, .44,
])

# ---------------------------------------------------------------- daily
dates = [START + timedelta(days=i) for i in range(DAYS)]
base_orders = 190.0

daily = []
cat_rev_total = {c: 0.0 for c, *_ in CATS}
cat_units_total = {c: 0.0 for c, *_ in CATS}
cat_ret_total = {c: 0.0 for c, *_ in CATS}
cat_can_total = {c: 0.0 for c, *_ in CATS}
cat_ord_total = {c: 0.0 for c, *_ in CATS}

for i, d in enumerate(dates):
    trend = 1.0 + 0.28 * (i / (DAYS - 1))                     # рост за период
    seasonal = 1.0 + 0.13 * np.sin(2 * np.pi * i / 30.4)     # месячная волна
    wk = WEEKDAY_K[d.weekday()]
    promo = 1.0 + (0.45 if i in (41, 42, 43, 118, 119, 120) else 0.0)  # распродажи
    noise = RNG.normal(1.0, 0.045)
    day_orders = base_orders * trend * seasonal * wk * promo * noise
    day_orders = max(40.0, day_orders)

    for name, share, aov, ret_rate, can_rate in CATS:
        cshare = share * RNG.normal(1.0, 0.035)
        orders = day_orders * cshare
        price = aov * RNG.normal(1.0, 0.03)
        revenue = orders * price
        units = orders * RNG.normal(1.14, 0.03)
        rr = ret_rate * RNG.normal(1.0, 0.10)
        cr = can_rate * RNG.normal(1.0, 0.10)
        returned = units * rr
        canceled = units * cr
        daily.append([
            d.isoformat(), name,
            round(float(revenue), 2), int(round(orders)),
            int(round(units)), int(round(returned)), int(round(canceled)),
        ])
        cat_rev_total[name] += revenue
        cat_units_total[name] += units
        cat_ret_total[name] += returned
        cat_can_total[name] += canceled
        cat_ord_total[name] += orders

# ---------------------------------------------------------------- skus
skus = []
for name, share, aov, ret_rate, can_rate in CATS:
    names = SKU_NAMES[name]
    n = len(names)
    # распределение по Парето: несколько лидеров, длинный хвост
    ranks = np.arange(1, n + 1)
    weights = 1.0 / (ranks ** 0.85)
    weights *= RNG.normal(1.0, 0.12, size=n)
    weights = np.clip(weights, 0.02, None)
    weights /= weights.sum()
    total_rev = cat_rev_total[name]
    total_ord = cat_ord_total[name]
    for k, sku in enumerate(names):
        rev = total_rev * weights[k]
        orders = total_ord * weights[k]
        units = orders * 1.14
        returned = units * ret_rate * RNG.normal(1.0, 0.15)
        skus.append([sku, name, round(rev, 2), int(round(orders)), int(round(units)), int(round(max(0, returned)))])

# ---------------------------------------------------------------- funnel
total_orders = sum(r[3] for r in daily)
buyouts = int(total_orders * 0.812)
carts = int(total_orders / 0.381)
visits = int(carts / 0.207)
impressions = int(visits / 0.192)
funnel = [
    ["Показы карточек", impressions],
    ["Переходы в карточку", visits],
    ["Добавления в корзину", carts],
    ["Оформленные заказы", total_orders],
    ["Выкупы", buyouts],
]

# ---------------------------------------------------------------- cohorts
cohorts = []
for c in range(8):
    month = (END.replace(day=1) - timedelta(days=30 * (7 - c))).strftime("%Y-%m")
    size = int(RNG.integers(1400, 3600) * (1 + 0.06 * c))
    vals = [100.0]
    for m in range(1, 8):
        prev = vals[-1]
        decay = RNG.normal(0.62, 0.05)
        v = max(3.5, prev * decay)
        vals.append(round(v, 1))
    cohorts.append([month, size, vals])

# ---------------------------------------------------------------- hours
hours = []
for dow in range(7):
    for h in range(24):
        v = HOUR_K[h] * WEEKDAY_K[dow] * 130 * RNG.normal(1.0, 0.06)
        hours.append([dow, h, max(0, int(round(v)))])

# ---------------------------------------------------------------- payload
payload = {
    "generatedAt": END.isoformat(),
    "period": {"from": START.isoformat(), "to": END.isoformat(), "days": DAYS},
    "cats": [c for c, *_ in CATS],
    "daily": daily,
    "skus": skus,
    "funnel": funnel,
    "cohorts": cohorts,
    "hours": hours,
}

DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "sales_daily.csv").write_text(
    "date,category,revenue,orders,units_sold,units_returned,units_canceled\n"
    + "\n".join(",".join(map(str, r)) for r in daily),
    encoding="utf-8",
)
(DATA_DIR / "skus.csv").write_text(
    "sku,category,revenue,orders,units_sold,units_returned\n"
    + "\n".join(",".join(str(x) for x in r) for r in skus),
    encoding="utf-8",
)

tpl = (HERE / "template.html").read_text(encoding="utf-8")
html = tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
OUT.write_text(html, encoding="utf-8")

# ---------------------------------------------------------------- report
tot_rev = sum(r[2] for r in daily)
tot_ord = sum(r[3] for r in daily)
tot_units = sum(r[4] for r in daily)
tot_ret = sum(r[5] for r in daily)
tot_can = sum(r[6] for r in daily)
print(f"период        : {START} .. {END} ({DAYS} дней)")
print(f"строк daily   : {len(daily)}")
print(f"выручка       : {tot_rev:,.0f} ₽".replace(",", " "))
print(f"заказы        : {tot_ord:,}".replace(",", " "))
print(f"средний чек   : {tot_rev / tot_ord:,.0f} ₽".replace(",", " "))
print(f"выкуп         : {tot_units / (tot_units + tot_ret + tot_can):.1%}")
print(f"возвраты      : {tot_ret / (tot_units + tot_ret + tot_can):.1%}")
print(f"SKU           : {len(skus)}")
print(f"файл          : {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")