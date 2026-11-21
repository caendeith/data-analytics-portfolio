"""Тесты чистых расчётов. Без сети, без базы, без снимка JSON.

Запуск:  python run_tests.py
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.metrics import (  # noqa: E402
    concentration_hhi,
    fmt_pct,
    fmt_usd,
    nearest_before_index,
    pct_change,
    share,
    top_n_share,
)


class PctChangeTest(unittest.TestCase):
    def test_growth(self):
        self.assertAlmostEqual(pct_change(100, 110), 10.0)

    def test_fall(self):
        self.assertAlmostEqual(pct_change(200, 150), -25.0)

    def test_zero_base_is_none_not_infinity(self):
        # Деление на ноль дало бы inf и сломанный график — отдаём None.
        self.assertIsNone(pct_change(0, 100))

    def test_none_input(self):
        self.assertIsNone(pct_change(None, 100))
        self.assertIsNone(pct_change(100, None))


class ShareTest(unittest.TestCase):
    def test_simple(self):
        self.assertAlmostEqual(share(25, 100), 25.0)

    def test_zero_total(self):
        self.assertIsNone(share(10, 0))


class HhiTest(unittest.TestCase):
    def test_monopoly_is_10000(self):
        self.assertAlmostEqual(concentration_hhi([500]), 10000.0)

    def test_two_equal_is_5000(self):
        self.assertAlmostEqual(concentration_hhi([50, 50]), 5000.0)

    def test_four_equal_is_2500(self):
        self.assertAlmostEqual(concentration_hhi([25, 25, 25, 25]), 2500.0)

    def test_ignores_zero_and_negative(self):
        self.assertAlmostEqual(concentration_hhi([100, 0, -5]), 10000.0)

    def test_empty(self):
        self.assertEqual(concentration_hhi([]), 0.0)


class TopNShareTest(unittest.TestCase):
    def test_top_two_of_four_equal_is_50(self):
        self.assertAlmostEqual(top_n_share([10, 10, 10, 10], 2), 50.0)

    def test_top_n_larger_than_list(self):
        self.assertAlmostEqual(top_n_share([1, 1], 10), 100.0)

    def test_empty_is_none(self):
        self.assertIsNone(top_n_share([], 3))


class NearestBeforeTest(unittest.TestCase):
    def setUp(self):
        self.points = [(100, 1.0), (200, 2.0), (300, 3.0)]

    def test_exact_hit(self):
        self.assertEqual(nearest_before_index(self.points, 200), 1)

    def test_between_points_takes_earlier(self):
        self.assertEqual(nearest_before_index(self.points, 250), 1)

    def test_before_first_is_none(self):
        self.assertIsNone(nearest_before_index(self.points, 50))

    def test_after_last_takes_last(self):
        self.assertEqual(nearest_before_index(self.points, 999), 2)


class FormatTest(unittest.TestCase):
    def test_usd_units(self):
        self.assertEqual(fmt_usd(2_825_300_000_000), "2.83T")
        # 422.6 млрд — это 422.60B: два знака после точки, как в подписях оси.
        self.assertEqual(fmt_usd(422_600_000_000), "422.60B")
        self.assertEqual(fmt_usd(12_300_000), "12.30M")
        self.assertEqual(fmt_usd(45_600), "45.60K")
        self.assertEqual(fmt_usd(999), "999")

    def test_usd_negative(self):
        self.assertEqual(fmt_usd(-1_500_000_000), "-1.50B")

    def test_usd_none(self):
        self.assertEqual(fmt_usd(None), "—")

    def test_pct_sign_always_present(self):
        self.assertEqual(fmt_pct(6.3), "+6.3%")
        self.assertEqual(fmt_pct(-6.3), "-6.3%")
        self.assertEqual(fmt_pct(0), "+0.0%")

    def test_pct_none(self):
        self.assertEqual(fmt_pct(None), "—")


if __name__ == "__main__":
    unittest.main(verbosity=2)
