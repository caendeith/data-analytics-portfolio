"""Сборка артефакта 06: синтетические отзывы → правила → метрики → отчёт.

Запуск:  python build.py
Опционально: OPENAI_API_KEY=... python build.py --with-llm   (сравнение бэкендов)

Почему правила, а не «просто LLM»: правила работают офлайн, бесплатны, объяснимы
и дают нижнюю планку. Сравнение с LLM включается флагом и честно помечается в
отчёте как «не запускалось», если ключа нет — а не выдаётся за результат.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))

from reviewlab import evaluate  # noqa: E402
from reviewlab.dataset import LABELS, N_REVIEWS, SEED, class_distribution, generate_reviews  # noqa: E402
from reviewlab.rules import classify_many  # noqa: E402
from reviewlab.text import truncate  # noqa: E402

OUT = ROOT / "out"


def write_csv(path: pathlib.Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def run_rules(reviews):
    """Прогнать правила по всем отзывам и собрать всё, что нужно отчёту."""
    predictions = classify_many([r.text for r in reviews])
    rows = []
    for review, pred in zip(reviews, predictions):
        rows.append(
            {
                "id": review.id,
                "marketplace": review.marketplace,
                "product_category": review.product_category,
                "rating": review.rating,
                "gold_label": review.gold_label,
                "pred_label": pred.label,
                "score": pred.score,
                "matched": "; ".join(pred.matched[:4]),
                "text": review.text,
                "correct": review.gold_label == pred.label,
            }
        )
    return rows


def llm_block(reviews, with_llm: bool) -> dict:
    """Сравнение с LLM. Без ключа возвращает честную заглушку, а не выдумку."""
    if not with_llm:
        return {"ran": False, "reason": "флаг --with-llm не передан"}

    from reviewlab.llm import LLMBackend, LLMUnavailable

    backend = LLMBackend.from_env()
    if not backend.available():
        return {"ran": False, "reason": "нет OPENAI_API_KEY в окружении"}

    try:
        labels = backend.predict_many([r.text for r in reviews])
    except LLMUnavailable as error:
        return {"ran": False, "reason": str(error)}

    golds = [r.gold_label for r in reviews]
    acc = evaluate.accuracy(golds, labels)
    return {
        "ran": True,
        "model": backend.model,
        "accuracy": round(acc * 100, 1),
        "calls": backend.calls,
        "errors": len(backend.errors),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Отчёт по классификации отзывов")
    parser.add_argument("--with-llm", action="store_true", help="сравнить с LLM (нужен ключ)")
    parser.add_argument("--limit", type=int, default=N_REVIEWS, help="сколько отзывов взять")
    args = parser.parse_args()

    reviews = generate_reviews(seed=SEED, n=args.limit)
    rows = run_rules(reviews)

    golds = [r.gold_label for r in reviews]
    preds = [r["pred_label"] for r in rows]

    matrix = evaluate.confusion_matrix(golds, preds, LABELS)
    classes = evaluate.per_class(golds, preds, LABELS)
    acc = evaluate.accuracy(golds, preds)
    macro = evaluate.macro_f1(classes)
    confusions = evaluate.top_confusions(golds, preds, limit=10)
    worst = evaluate.worst_class(classes)

    # Примеры провалов: по два на каждую частую путаницу, с объяснением, что сработало.
    by_pair: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if not row["correct"]:
            by_pair.setdefault((row["gold_label"], row["pred_label"]), []).append(row)
    failure_samples = []
    seen_texts: set[str] = set()
    for pair in confusions[:8]:
        key = (pair["gold"], pair["pred"])
        for row in by_pair.get(key, []):
            if len([s for s in failure_samples if s["gold"] == pair["gold"] and s["pred"] == pair["pred"]]) >= 2:
                break
            # Один и тот же текст не показываем дважды: он мог попасть в две путаницы.
            if row["text"] in seen_texts:
                continue
            seen_texts.add(row["text"])
            failure_samples.append(
                {
                    "gold": pair["gold"],
                    "pred": pair["pred"],
                    "text": truncate(row["text"], 220),
                    "matched": row["matched"] or "—",
                    "rating": row["rating"],
                }
            )

    llm = llm_block(reviews, args.with_llm)

    OUT.mkdir(exist_ok=True)
    write_csv(OUT / "reviews_scored.csv", rows)
    write_csv(OUT / "per_class.csv", classes)
    write_csv(OUT / "failures.csv", [r for r in rows if not r["correct"]])

    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "seed": SEED,
            "n_reviews": len(reviews),
            "n_errors": sum(not r["correct"] for r in rows),
            "backend": "правила (regex + окно отрицания)",
        },
        "kpi": {
            "accuracy": round(acc * 100, 1),
            "macro_f1": round(macro * 100, 1),
            "errors": sum(not r["correct"] for r in rows),
            "total": len(reviews),
            "worst_label": worst["label"],
            "worst_f1": round(worst["f1"] * 100, 1),
            "worst_support": worst["support"],
        },
        "labels": list(LABELS),
        "confusion": matrix,
        "classes": [
            {
                "label": c["label"],
                "precision": round(c["precision"] * 100, 1),
                "recall": round(c["recall"] * 100, 1),
                "f1": round(c["f1"] * 100, 1),
                "support": c["support"],
                "errors": c["support"] - c["tp"],
            }
            for c in classes
        ],
        "distribution": class_distribution(reviews),
        "confusions": confusions,
        "failure_samples": failure_samples,
        "llm": llm,
        "rules_note": {
            "classes": len(LABELS),
            "complaint_classes": len(LABELS) - 1,
            "synonyms_handled": "отрицание в окне 3 слов, эмодзи, опечатки, ё/е",
        },
    }

    (OUT / "payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    template = (ROOT / "template.html").read_text(encoding="utf-8")
    html = template.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    (ROOT / "report.html").write_text(html, encoding="utf-8")

    print(f"отзывов: {len(reviews)}  классов: {len(LABELS)}  ошибок: {payload['meta']['n_errors']}")
    print(f"accuracy {payload['kpi']['accuracy']}%  macro-F1 {payload['kpi']['macro_f1']}%")
    print(f"худший класс: {worst['label']} (F1 {payload['kpi']['worst_f1']}%, support {worst['support']})")
    print(f"LLM: {'сравнён' if llm.get('ran') else 'не запускался — ' + llm.get('reason', '')}")
    print(f"отчёт: {ROOT / 'report.html'} ({(ROOT / 'report.html').stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
