"""Запуск тестов без pytest: python run_tests.py

pytest в окружении не установлен, а тащить зависимость ради 20 тестов
незачем — unittest входит в стандартную библиотеку.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))

loader = unittest.TestLoader()
suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)

passed = result.testsRun - len(result.failures) - len(result.errors)
print(f"\nИТОГО: {passed}/{result.testsRun} прошло, ошибок {len(result.errors)}, провалов {len(result.failures)}")

sys.exit(0 if result.wasSuccessful() else 1)
