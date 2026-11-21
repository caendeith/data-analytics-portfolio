"""Сборка артефакта 05 «On-chain аналитика»: JSON-снимок → SQLite → SQL → дашборд.

Запуск:  python build.py
Данные:  data/*.json (снимок публичного API DefiLlama, скачанный bin/fetch.mjs)

Скрипт намеренно разделён на слои: сначала сырой снимок кладётся в базу как есть,
потом всё считается SQL-запросами из queries/, и только потом рисуется дашборд.
Так видно, что цифры в дашборде — результат запросов, а не украшение.
"""

from __future__ import annotations

import csv
import json
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from src.metrics import (  # noqa: E402
    concentration_hhi,
    fmt_pct,
    fmt_usd,
    nearest_before_index,
    pct_change,
    top_n_share,
)

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "out"
QUERIES = ROOT / "queries"
DB_PATH = OUT / "onchain.db"

DAY = 86400


def load(name: str):
    path = DATA / name
    if not path.exists():
        raise SystemExit(f"нет файла {path} — сначала запусти: node bin/fetch.mjs")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- слой 1: база

def build_db(stable_raw, chains_raw, protocols_raw) -> sqlite3.Connection:
    OUT.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.executescript(
        """
        CREATE TABLE stablecoin_daily (day TEXT, ts INTEGER, usd REAL);
        CREATE TABLE chains (name TEXT, tvl REAL);
        CREATE TABLE protocols (
            name TEXT, category TEXT, chain TEXT,
            tvl REAL, change_1d REAL, change_7d REAL, chains_count INTEGER
        );
        """
    )

    rows = []
    for point in stable_raw:
        ts = int(point["date"])
        usd = (point.get("totalCirculatingUSD") or {}).get("peggedUSD")
        if usd is None:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        rows.append((day, ts, float(usd)))
    con.executemany("INSERT INTO stablecoin_daily VALUES (?,?,?)", rows)

    con.executemany(
        "INSERT INTO chains VALUES (?,?)",
        [(c.get("name") or "—", float(c.get("tvl") or 0)) for c in chains_raw],
    )

    prows = []
    for p in protocols_raw:
        prows.append(
            (
                p.get("name") or "—",
                p.get("category") or "",
                p.get("chain") or "",
                float(p.get("tvl") or 0),
                p.get("change_1d"),
                p.get("change_7d"),
                len(p.get("chains") or []),
            )
        )
    con.executemany("INSERT INTO protocols VALUES (?,?,?,?,?,?,?)", prows)
    con.commit()
    return con


def run_query(con: sqlite3.Connection, name: str, **params):
    sql = (QUERIES / name).read_text(encoding="utf-8")
    con.row_factory = sqlite3.Row
    cur = con.execute(sql, params) if params else con.execute(sql)
    return [dict(r) for r in cur.fetchall()]


# ------------------------------------------------------- слой 2: производные

def stablecoin_block(con: sqlite3.Connection) -> dict:
    con.row_factory = sqlite3.Row
    points = [
        (int(r["ts"]), float(r["usd"]), r["day"])
        for r in con.execute("SELECT ts, usd, day FROM stablecoin_daily ORDER BY ts")
    ]
    if not points:
        raise SystemExit("пустая таблица stablecoin_daily")

    latest_ts, latest_usd, latest_day = points[-1]
    deltas = {}
    for label, days in (("7d", 7), ("30d", 30), ("90d", 90), ("365d", 365)):
        idx = nearest_before_index([(ts, usd) for ts, usd, _ in points], latest_ts - days * DAY)
        deltas[label] = pct_change(points[idx][1], latest_usd) if idx is not None else None

    # ряд за последний год, прореженный до ~180 точек — столько рисует SVG без тормозов
    cutoff = latest_ts - 365 * DAY
    year = [p for p in points if p[0] >= cutoff]
    step = max(1, len(year) // 180)
    series = [{"d": d, "v": round(v, 0)} for _, v, d in year[::step]]
    # Прореживание могло срезать последние дни, и тогда конец графика не совпадёт
    # с KPI-карточкой. Последнюю точку ставим принудительно.
    if series:
        series[-1] = {"d": latest_day, "v": round(latest_usd, 0)}
    else:
        series = [{"d": latest_day, "v": round(latest_usd, 0)}]

    return {
        "latest": latest_usd,
        "latest_day": latest_day,
        "deltas": deltas,
        "series": series,
        "first_day": year[0][2] if year else latest_day,
    }


def protocols_block(con: sqlite3.Connection) -> dict:
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT name, tvl, change_7d FROM protocols WHERE tvl > 0")]
    total = sum(r["tvl"] for r in rows)
    tvls = [r["tvl"] for r in rows]
    return {
        "total_tvl": total,
        "protocols": len(rows),
        "hhi": concentration_hhi(tvls),
        "top10_share": top_n_share(tvls, 10),
        "top50_share": top_n_share(tvls, 50),
    }


def write_csv(path: pathlib.Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------- слой 3: дашборд

def render(payload: dict) -> pathlib.Path:
    template = (ROOT / "template.html").read_text(encoding="utf-8")
    html = template.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    out = ROOT / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    return out


def main() -> None:
    stable_raw = load("stablecoins_all.json")
    chains_raw = load("chains.json")
    protocols_raw = load("protocols.json")

    con = build_db(stable_raw, chains_raw, protocols_raw)

    stable = stablecoin_block(con)
    protos = protocols_block(con)

    total_chains_tvl = sum(float(c.get("tvl") or 0) for c in chains_raw)
    chains_rows = run_query(con, "chains_top.sql", total=total_chains_tvl)
    categories = run_query(con, "categories.sql")
    top_protocols = run_query(con, "protocols_top.sql")
    movers_all = run_query(con, "movers.sql")

    OUT.mkdir(exist_ok=True)
    write_csv(OUT / "stablecoin_daily.csv", stable["series"])
    write_csv(OUT / "chains_top.csv", chains_rows)
    write_csv(OUT / "categories.csv", categories)
    write_csv(OUT / "protocols_top.csv", top_protocols)
    write_csv(OUT / "movers.csv", movers_all[:10] + movers_all[-10:])

    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "source": "DefiLlama public API (stablecoins.llama.fi, api.llama.fi)",
            "snapshot_day": stable["latest_day"],
        },
        "kpi": {
            "stable_usd": stable["latest"],
            "stable_usd_fmt": fmt_usd(stable["latest"]),
            "d7": stable["deltas"]["7d"],
            "d30": stable["deltas"]["30d"],
            "d90": stable["deltas"]["90d"],
            "d365": stable["deltas"]["365d"],
            "d7_fmt": fmt_pct(stable["deltas"]["7d"]),
            "d30_fmt": fmt_pct(stable["deltas"]["30d"]),
            "d90_fmt": fmt_pct(stable["deltas"]["90d"]),
            "d365_fmt": fmt_pct(stable["deltas"]["365d"]),
            "defi_tvl_fmt": fmt_usd(protos["total_tvl"]),
            "defi_tvl": protos["total_tvl"],
            "protocols": protos["protocols"],
            "top10_share": round(protos["top10_share"] or 0, 1),
            "top50_share": round(protos["top50_share"] or 0, 1),
            "hhi": round(protos["hhi"], 0),
        },
        "stable_series": stable["series"],
        "stable_first_day": stable["first_day"],
        "chains": chains_rows,
        "categories": [
            {
                "name": c["category"],
                "tvl": round(c["tvl"] or 0, 0),
                "tvl_fmt": fmt_usd(c["tvl"]),
                "protocols": c["protocols"],
                "share": round((c["tvl"] or 0) / protos["total_tvl"] * 100, 1)
                if protos["total_tvl"]
                else 0,
            }
            for c in categories
        ],
        "top_protocols": [
            {
                "name": p["name"],
                "category": p["category"] or "—",
                "tvl": round(p["tvl"], 0),
                "tvl_fmt": fmt_usd(p["tvl"]),
                "ch7": p["change_7d"],
            }
            for p in top_protocols
        ],
        "movers_up": [
            {"name": m["name"], "category": m["category"] or "—", "tvl_fmt": fmt_usd(m["tvl"]), "ch7": m["change_7d"]}
            for m in movers_all[:10]
        ],
        "movers_down": [
            {"name": m["name"], "category": m["category"] or "—", "tvl_fmt": fmt_usd(m["tvl"]), "ch7": m["change_7d"]}
            for m in movers_all[-10:][::-1]
        ],
    }

    (OUT / "payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    dash = render(payload)

    print(f"снимок: {payload['meta']['snapshot_day']}  протоколов: {protos['protocols']}")
    print(f"стейблкоины: {payload['kpi']['stable_usd_fmt']}  за 30 дней {payload['kpi']['d30_fmt']}  за год {payload['kpi']['d365_fmt']}")
    print(f"DeFi TVL: {payload['kpi']['defi_tvl_fmt']}  топ-10 протоколов: {payload['kpi']['top10_share']}%  HHI: {payload['kpi']['hhi']:.0f}")
    print(f"дашборд: {dash}  ({dash.stat().st_size / 1024:.0f} KB)")
    con.close()


if __name__ == "__main__":
    main()
