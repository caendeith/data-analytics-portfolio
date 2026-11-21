"""Тесты артефакта 06: метрики, нормализация, правила, детерминизм датасета.

Без сети, без ключей, без прогона всего датасета (кроме одного теста на состав).
Запуск: python run_tests.py
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from reviewlab import evaluate  # noqa: E402
from reviewlab.dataset import LABELS, allocate_counts, generate_reviews  # noqa: E402
from reviewlab.llm import build_request_body, parse_label  # noqa: E402
from reviewlab.rules import classify  # noqa: E402
from reviewlab.text import is_negated, normalize_text, strip_emoji, tokenize  # noqa: E402


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.labels = ("a", "b", "c")

    def test_accuracy_known(self):
        self.assertAlmostEqual(evaluate.accuracy(["a", "a", "b", "b"], ["a", "b", "b", "b"]), 0.75)

    def test_accuracy_empty_raises(self):
        with self.assertRaises(ValueError):
            evaluate.accuracy([], [])

    def test_confusion_matrix_shape_and_diagonal(self):
        m = evaluate.confusion_matrix(["a", "b", "c"], ["a", "c", "c"], self.labels)
        self.assertEqual(len(m), 3)
        self.assertEqual(m[0], [1, 0, 0])
        self.assertEqual(m[1], [0, 0, 1])
        self.assertEqual(m[2], [0, 0, 1])

    def test_confusion_unknown_label_raises(self):
        with self.assertRaises(ValueError):
            evaluate.confusion_matrix(["a"], ["zzz"], self.labels)

    def test_per_class_precision_recall(self):
        rows = {r["label"]: r for r in evaluate.per_class(["a", "a", "b"], ["a", "b", "b"], self.labels)}
        # класс a: 1 верно, 1 промах → precision 1.0, recall 0.5
        self.assertAlmostEqual(rows["a"]["precision"], 1.0)
        self.assertAlmostEqual(rows["a"]["recall"], 0.5)
        self.assertEqual(rows["a"]["support"], 2)

    def test_per_class_empty_predictions_do_not_crash(self):
        rows = {r["label"]: r for r in evaluate.per_class(["a"], ["b"], self.labels)}
        self.assertEqual(rows["c"]["precision"], 0.0)
        self.assertEqual(rows["c"]["support"], 0)

    def test_macro_f1_averages_classes_not_examples(self):
        rows = evaluate.per_class(["a", "a", "a", "b"], ["a", "a", "a", "a"], self.labels)
        macro = evaluate.macro_f1(rows)
        # Класс a: precision 1.0, recall 0.75 → F1 ≈ 0.857; b и c — нули.
        # Среднее по классам ≈ 0.286, хотя accuracy 0.75: именно поэтому macro.
        self.assertAlmostEqual(macro, sum(r["f1"] for r in rows) / 3, places=6)
        self.assertLess(macro, evaluate.accuracy(["a", "a", "a", "b"], ["a", "a", "a", "a"]))

    def test_macro_f1_empty(self):
        self.assertEqual(evaluate.macro_f1([]), 0.0)

    def test_top_confusions_sorted(self):
        rows = evaluate.top_confusions(["a", "a", "b"], ["b", "b", "a"], limit=5)
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual((rows[0]["gold"], rows[0]["pred"]), ("a", "b"))

    def test_worst_class_ignores_absent(self):
        rows = evaluate.per_class(["a", "a"], ["a", "a"], self.labels)
        self.assertEqual(evaluate.worst_class(rows)["label"], "a")

    def test_errors_by_class(self):
        self.assertEqual(evaluate.errors_by_class(["a", "a", "b"], ["a", "c", "b"]), {"a": 1})


class TextTest(unittest.TestCase):
    def test_normalize_lowercases_and_fixes_yo(self):
        self.assertEqual(normalize_text("ЁЖИК Не ПОДОШЁЛ"), "ежик не подошел")

    def test_normalize_collapses_whitespace(self):
        self.assertEqual(normalize_text("размер   не\tмой"), "размер не мой")

    def test_strip_emoji(self):
        # Пробелы на месте эмодзи остаются, поэтому сравниваем после схлопывания.
        self.assertEqual(" ".join(strip_emoji("плохо 😡🔥 но ок 🙂").split()), "плохо но ок")

    def test_tokenize_keeps_words(self):
        self.assertIn("доставка", tokenize("Доставка быстрая!"))

    def test_negation_detected(self):
        text = normalize_text("размер не подошел")
        start = text.index("подошел")
        self.assertTrue(is_negated(text, start))

    def test_negation_not_confused_by_far_cue(self):
        text = normalize_text("не знаю, но размер подошел")
        start = text.index("подошел")
        self.assertFalse(is_negated(text, start, window=2))


class RulesTest(unittest.TestCase):
    def test_size_complaint(self):
        self.assertEqual(classify("Брюки не подошли по размеру, надо было брать больше").label, "размер")

    def test_delivery_complaint(self):
        self.assertEqual(classify("Посылку ждали две недели, доставка ужасная").label, "доставка")

    def test_quality_complaint(self):
        self.assertEqual(classify("Товар с браком, шов разошелся на второй день").label, "брак_качество")

    def test_neutral_review_has_no_label(self):
        self.assertEqual(classify("Спасибо, все как на фото, доволен покупкой").label, "нет_жалобы")

    def test_prediction_explains_itself(self):
        pred = classify("Платье не подошло по размеру, мало")
        self.assertTrue(pred.matched, "должны остаться сработавшие фрагменты")
        self.assertGreater(pred.score, 0)

    def test_score_never_negative(self):
        self.assertGreaterEqual(classify("брак размер доставка цена упаковка").score, 0)


class DatasetTest(unittest.TestCase):
    def test_same_seed_same_data(self):
        a = generate_reviews(seed=42, n=20)
        b = generate_reviews(seed=42, n=20)
        self.assertEqual([r.text for r in a], [r.text for r in b])

    def test_different_seed_different_texts(self):
        a = generate_reviews(seed=1, n=20)
        b = generate_reviews(seed=2, n=20)
        self.assertNotEqual([r.text for r in a], [r.text for r in b])

    def test_gold_labels_are_known(self):
        for review in generate_reviews(seed=7, n=50):
            self.assertIn(review.gold_label, LABELS)

    def test_ratings_within_range(self):
        for review in generate_reviews(seed=7, n=50):
            self.assertGreaterEqual(review.rating, 1)
            self.assertLessEqual(review.rating, 5)

    def test_allocate_counts_sums_exactly(self):
        counts = allocate_counts(1000, {"a": 0.3, "b": 0.3, "c": 0.4})
        self.assertEqual(sum(counts.values()), 1000)

    def test_allocate_counts_is_deterministic(self):
        self.assertEqual(allocate_counts(37, {"a": 0.5, "b": 0.5}), allocate_counts(37, {"a": 0.5, "b": 0.5}))


class LLMParseTest(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(parse_label('{"label": "доставка"}'), "доставка")

    def test_markdown_fence(self):
        self.assertEqual(parse_label('```json\n{"label": "цена"}\n```'), "цена")

    def test_bare_string(self):
        self.assertEqual(parse_label("размер"), "размер")

    def test_alias_mapped(self):
        self.assertEqual(parse_label('{"label": "качество"}'), "брак_качество")

    def test_unknown_falls_back_to_no_complaint(self):
        self.assertEqual(parse_label('{"label": "что-то своё"}'), "нет_жалобы")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_label("   ")

    def test_request_body_shape(self):
        body = build_request_body("текст", model="m")
        self.assertEqual(body["model"], "m")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(len(body["messages"]), 2)
        self.assertIn("текст", body["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
