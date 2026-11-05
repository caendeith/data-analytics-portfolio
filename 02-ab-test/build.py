"""
Артефакт 2 — A/B-тест: анализ эксперимента целиком.

Кейс: снижение порога бесплатной доставки с 3 000 ₽ до 2 000 ₽
на маркетплейсе. Датасет синтетический (seed фиксирован), но
механика реалистичная: эффект на конверсию зависит от суммы корзины,
субсидия доставки начисляется по каждому фактическому заказу,
доходность считается по чистой марже, а не по выручке.

Запуск:  PYTHONIOENCODING=utf-8 python build.py
Результат: results.json, report.html, data/*.csv

Все ключевые числа ниже НЕ вписаны руками — они вычислены из данных.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from scipy import stats

RNG = np.random.default_rng(20261104)
HERE = Path(__file__).parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

# ------------------------------------------------------------------ дизайн
N_SESSIONS = 300_000
ALPHA = 0.05
POWER = 0.80
DAYS = 14
END = date(2026, 11, 4)
START = END - timedelta(days=DAYS - 1)

THRESHOLD_CONTROL = 3000.0   # бесплатная доставка от 3 000 ₽
THRESHOLD_TREAT = 2000.0     # ... от 2 000 ₽
DELIVERY_COST = 250.0        # себестоимость доставки, ₽
GROSS_MARGIN = 0.27          # маржинальность товара
RETURN_HANDLING = 0.09       # издержки на возврат, доля от суммы заказа
BASE_CR = 0.030              # базовая конверсия сессии в заказ

# ------------------------------------------------------------------ сессии
arm = RNG.random(N_SESSIONS) < 0.5                      # True = treatment
is_new = RNG.random(N_SESSIONS) < 0.38
device = np.where(RNG.random(N_SESSIONS) < 0.72, "mobile", "desktop")
region = RNG.choice(["Москва", "Санкт-Петербург", "Регионы"],
                    size=N_SESSIONS, p=[0.24, 0.11, 0.65])

V_MULT = {"new": 0.88, "ret": 1.12, "mobile": 1.00, "desktop": 1.15,
          "Москва": 1.18, "Санкт-Петербург": 1.10, "Регионы": 0.92}
CR_MULT = {"new": 0.85, "ret": 1.15, "mobile": 0.92, "desktop": 1.18,
           "Москва": 1.08, "Санкт-Петербург": 1.06, "Регионы": 1.00}

v_mult = np.where(is_new, V_MULT["new"], V_MULT["ret"]) * np.where(
    device == "mobile", V_MULT["mobile"], V_MULT["desktop"]) * np.array(
    [V_MULT[r] for r in region])
cr_mult = np.where(is_new, CR_MULT["new"], CR_MULT["ret"]) * np.where(
    device == "mobile", CR_MULT["mobile"], CR_MULT["desktop"]) * np.array(
    [CR_MULT[r] for r in region])

# сумма корзины, которую клиент набрал бы (не зависит от плеча)
basket = np.exp(RNG.normal(7.90, 0.72, N_SESSIONS)) * v_mult
basket = np.maximum(basket, 400.0)

# баз. конверсия; эффект бесплатной доставки сильнее на малых корзинах
p_base = np.clip(BASE_CR * cr_mult * RNG.normal(1.0, 0.10, N_SESSIONS), 0.001, 0.65)
k = 0.16 / (1.0 + (basket / 2600.0) ** 1.6)      # относительный прирост CR
p = np.where(arm, np.minimum(p_base * (1.0 + k), 0.90), p_base)

converted = RNG.random(N_SESSIONS) < p
revenue = np.where(converted, basket, 0.0)
orders = converted.astype(np.int64)

# субсидия доставки — компания платит за доставку, только когда она бесплатна
# для клиента, т.е. когда сумма заказа ДОСТИГАЕТ порога
thr = np.where(arm, THRESHOLD_TREAT, THRESHOLD_CONTROL)
subsidy = np.where(converted & (basket >= thr), DELIVERY_COST, 0.0)

# возвраты и отмены
ret_rate = np.where(arm, 0.090, 0.082) * RNG.normal(1.0, 0.08, N_SESSIONS)
returned = converted & (RNG.random(N_SESSIONS) < ret_rate)
refund = np.where(returned, basket, 0.0)

# чистая маржа по заказу
net = (GROSS_MARGIN * revenue
       - subsidy
       - np.where(returned, RETURN_HANDLING * basket, 0.0))

# дата сессии — равномерно по периоду
day_idx = RNG.integers(0, DAYS, N_SESSIONS)
dates = np.array([(START + timedelta(days=int(i))).isoformat() for i in range(DAYS)])
sess_date = dates[day_idx]

# ------------------------------------------------------------------ метрики
def arm_slices(a): return ~arm if a == 0 else arm

n1 = int((~arm).sum()); n2 = int(arm.sum())
x1 = int(orders[~arm].sum()); x2 = int(orders[arm].sum())
p1 = x1 / n1; p2 = x2 / n2

def two_prop(x1, n1, x2, n2):
    p1_, p2_ = x1 / n1, x2 / n2
    pooled = (x1 + x2) / (n1 + n2)
    se_p = np.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    z = (p2_ - p1_) / se_p if se_p > 0 else 0.0
    pv = 2 * stats.norm.sf(abs(z))
    se_u = np.sqrt(p1_ * (1 - p1_) / n1 + p2_ * (1 - p2_) / n2)
    d = p2_ - p1_
    return dict(control=p1_, treat=p2_, diff=d, rel=(d / p1_ if p1_ else 0.0),
                z=float(z), p=float(pv),
                ci_low=float(d - 1.96 * se_u), ci_high=float(d + 1.96 * se_u))

cr = two_prop(x1, n1, x2, n2)

def welch(a, b):
    t, pv = stats.ttest_ind(b, a, equal_var=False)
    m1, m2 = a.mean(), b.mean()
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    d = m2 - m1
    return dict(control=float(m1), treat=float(m2), diff=float(d),
                rel=float(d / m1) if m1 else 0.0, p=float(pv),
                ci_low=float(d - 1.96 * se), ci_high=float(d + 1.96 * se))

arpu = welch(revenue[~arm], revenue[arm])
aov = welch(revenue[converted & ~arm], revenue[converted & arm])
netps = welch(net[~arm], net[arm])
sub_ps = welch(subsidy[~arm], subsidy[arm])
refund_ps = welch(refund[~arm], refund[arm])

ret_c = int(returned[~arm].sum()) / x1
ret_t = int(returned[arm].sum()) / x2
ret_test = two_prop(int(returned[~arm].sum()), x1, int(returned[arm].sum()), x2)

# SRM — проверка соотношения размеров групп
chi2, srm_p = stats.chisquare([n1, n2], f_exp=[N_SESSIONS / 2] * 2)

# MDE: насколько малый эффект тест способен поймать при этих объёмах
def mde_two_prop(p0, n_per_arm, alpha=ALPHA, power=POWER):
    za = stats.norm.ppf(1 - alpha / 2)
    zb = stats.norm.ppf(power)
    se = np.sqrt(2 * p0 * (1 - p0) / n_per_arm)
    return float((za + zb) * se)

mde_abs = mde_two_prop(p1, min(n1, n2))
mde_rel = mde_abs / p1

def n_required(p0, rel, alpha=ALPHA, power=POWER):
    p1_ = p0 * (1 + rel)
    za = stats.norm.ppf(1 - alpha / 2)
    zb = stats.norm.ppf(power)
    num = (za * np.sqrt(2 * p0 * (1 - p0)) + zb * np.sqrt(p0 * (1 - p0) + p1_ * (1 - p1_))) ** 2
    return float(num / (p1_ - p0) ** 2)

n_req_5 = n_required(p1, 0.05)

# ------------------------------------------------------------------ сегменты
def segment_stats(seg_arr, label):
    out = []
    for val in sorted(set(seg_arr.tolist())):
        m = seg_arr == val
        m1 = m & ~arm; m2 = m & arm
        s1 = int(m1.sum()); s2 = int(m2.sum())
        if s1 < 200 or s2 < 200:
            continue
        o1 = int(orders[m1].sum()); o2 = int(orders[m2].sum())
        r = two_prop(o1, s1, o2, s2)
        r.update(segment=label, value=str(val), n_control=s1, n_treat=s2,
                 orders_control=o1, orders_treat=o2)
        out.append(r)
    return out

segments = []
segments += segment_stats(np.where(is_new, "Новые", "Повторные"), "Тип клиента")
segments += segment_stats(device, "Устройство")
segments += segment_stats(region, "Регион")
# поправка на множественные сравнения
m_tests = len(segments)
for s in segments:
    s["p_adj"] = float(min(1.0, s["p"] * m_tests))

# ------------------------------------------------------------------ динамика
daily = []
for d in dates:
    row = {"date": d}
    for lbl, m in (("control", (~arm) & (sess_date == d)), ("treat", arm & (sess_date == d))):
        ns = int(m.sum()); os_ = int(orders[m].sum())
        row[lbl] = {"sessions": ns, "orders": os_,
                    "cr": os_ / ns if ns else 0.0,
                    "arpu": float(revenue[m].mean()) if ns else 0.0,
                    "aov": float(revenue[m & converted].mean()) if os_ else 0.0,
                    "net": float(net[m].mean()) if ns else 0.0}
    daily.append(row)

# ------------------------------------------------------------------ распределение чеков
bins = [0, 1000, 2000, 3000, 4000, 5000, 7000, 10000, 15000, 1e9]
labels = ["<1 000", "1–2 тыс", "2–3 тыс", "3–4 тыс", "4–5 тыс",
          "5–7 тыс", "7–10 тыс", "10–15 тыс", ">15 тыс"]
hist = []
for lbl, m in (("control", converted & ~arm), ("treat", converted & arm)):
    h, _ = np.histogram(revenue[m], bins=bins)
    hist.append({"arm": lbl, "counts": [int(v) for v in h]})
hist_labels = labels

subs_by_bucket = []
for lbl, m in (("control", converted & ~arm), ("treat", converted & arm)):
    h, _ = np.histogram(revenue[m], bins=bins, weights=subsidy[m])
    subs_by_bucket.append({"arm": lbl, "rub": [float(v) for v in h]})

# ------------------------------------------------------------------ итог
def verdict():
    sig_cr = cr["p"] < ALPHA and cr["diff"] > 0
    net_ok = netps["p"] < ALPHA and netps["diff"] > 0
    net_bad = netps["p"] < ALPHA and netps["diff"] < 0
    if not sig_cr:
        return "no_effect"
    if net_ok:
        return "ship"
    if net_bad:
        return "dont_ship"
    return "iterate"

v = verdict()

results = {
    "meta": {
        "case": "Снижение порога бесплатной доставки: 3 000 ₽ → 2 000 ₽",
        "period": {"from": START.isoformat(), "to": END.isoformat(), "days": DAYS},
        "sessions": N_SESSIONS, "n_control": n1, "n_treat": n2,
        "alpha": ALPHA, "power": POWER,
        "mde_abs": mde_abs, "mde_rel": mde_rel, "n_required_5pct": n_req_5,
        "srm": {"control": n1, "treat": n2, "chi2": float(chi2), "p": float(srm_p),
                "ok": bool(srm_p > 0.001)},
        "assumptions": {
            "delivery_cost": DELIVERY_COST, "gross_margin": GROSS_MARGIN,
            "return_handling": RETURN_HANDLING,
            "threshold_control": THRESHOLD_CONTROL,
            "threshold_treat": THRESHOLD_TREAT,
        },
    },
    "primary": {"name": "Конверсия сессии в заказ (CR)", **cr,
                "orders_control": x1, "orders_treat": x2,
                "sessions_control": n1, "sessions_treat": n2},
    "arpu": {"name": "Выручка на сессию (ARPU, ₽)", **arpu},
    "aov": {"name": "Средний чек (AOV, ₽)", **aov},
    "net": {"name": "Чистая маржа на сессию (₽)", **netps},
    "subsidy": {"name": "Субсидия доставки на сессию (₽)", **sub_ps},
    "refund": {"name": "Возвраты на сессию (₽)", **refund_ps},
    "returns": {"name": "Доля возвратов от заказов", "control": ret_c, "treat": ret_t,
                "diff": ret_t - ret_c, "rel": (ret_t - ret_c) / ret_c,
                "p": ret_test["p"], "ci_low": ret_test["ci_low"], "ci_high": ret_test["ci_high"]},
    "segments": segments, "m_tests": m_tests,
    "daily": daily, "hist": hist, "hist_labels": hist_labels,
    "subs_by_bucket": subs_by_bucket,
    "verdict": v,
}

(HERE / "results.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

# отчёт собирается из шаблона: все числа подставляет JS из results.json
tpl_path = HERE / "template.html"
if tpl_path.exists():
    tpl = tpl_path.read_text(encoding="utf-8")
    html = tpl.replace("__RESULTS__",
                       json.dumps(results, ensure_ascii=False, separators=(",", ":")))
    (HERE / "report.html").write_text(html, encoding="utf-8")

# ------------------------------------------------------------------ csv
rows = ["date,arm,sessions,orders,cr,arpu,aov,net_per_session"]
for r in daily:
    for k in ("control", "treat"):
        d = r[k]
        rows.append(f"{r['date']},{k},{d['sessions']},{d['orders']},"
                    f"{d['cr']:.6f},{d['arpu']:.2f},{d['aov']:.2f},{d['net']:.2f}")
(DATA / "daily.csv").write_text("\n".join(rows), encoding="utf-8")

# выборка сессий для воспроизводимости
idx = RNG.choice(N_SESSIONS, 20000, replace=False)
sample = ["session_id,arm,date,is_new,device,region,basket,converted,revenue,subsidy,returned,net"]
for i in idx:
    sample.append(",".join([
        f"s{int(i):06d}", "T" if arm[i] else "C", sess_date[i],
        "new" if is_new[i] else "ret", device[i], region[i],
        f"{basket[i]:.2f}", str(int(orders[i])), f"{revenue[i]:.2f}",
        f"{subsidy[i]:.2f}", str(int(returned[i])), f"{net[i]:.2f}",
    ]))
(DATA / "sessions_sample.csv").write_text("\n".join(sample), encoding="utf-8")

# ------------------------------------------------------------------ печать
def pp(name, d, unit="", is_pct=False):
    f = (lambda x: f"{x*100:+.2f} п.п.") if is_pct else (lambda x: f"{x:+,.2f}")
    print(f"{name:<38} {f(d['control']):>12} → {f(d['treat']):>12}   "
          f"Δ={f(d['diff']):>11}  {d['rel']*100:+.1f}%  p={d['p']:.2e}")

print("=" * 100)
print(f"сессий: {N_SESSIONS:,}  контроль {n1:,} / тест {n2:,}".replace(",", " "))
print(f"SRM: chi2={chi2:.3f}, p={srm_p:.3f} -> {'OK' if srm_p > 0.001 else 'ПРОБЛЕМА'}")
print(f"MDE при этих объёмах: {mde_abs*100:.3f} п.п. = {mde_rel*100:.1f}% относительно")
print(f"нужно сессий на плечо для MDE 5%: {n_req_5:,.0f}".replace(",", " "))
print("-" * 100)
pp("CR (конверсия в заказ)", cr, is_pct=True)
pp("ARPU, ₽", arpu)
pp("AOV, ₽", aov)
pp("Субсидия доставки на сессию, ₽", sub_ps)
pp("Чистая маржа на сессию, ₽", netps)
print("-" * 100)
print(f"возвраты: {ret_c*100:.2f}% -> {ret_t*100:.2f}%  p={ret_test['p']:.2e}")
print("-" * 100)
for s in segments:
    print(f"  {s['segment']:<16} {s['value']:<18} {s['control']*100:.2f}% → "
          f"{s['treat']*100:.2f}%  {s['rel']*100:+.1f}%  p={s['p']:.1e}  p_adj={s['p_adj']:.1e}")
print("-" * 100)
print(f"ВЕРДИКТ: {v}")
print("=" * 100)