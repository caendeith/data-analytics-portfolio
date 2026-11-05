"""Прогон всех проверок проекта одной командой.

    python run_tests.py

Возвращает 0, если всё зелёное, и 1, если что-то упало — так это можно
подключить в CI без pytest.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUITES = ["tests/test_common.py", "tests/test_dag_structure.py"]


def main() -> int:
    results = []
    for suite in SUITES:
        path = ROOT / suite
        print(f"\n=== {suite} " + "=" * (60 - len(suite)))
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=ROOT,
            env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
        )
        results.append((suite, proc.returncode))

    print("\n" + "=" * 70)
    failed = [s for s, code in results if code != 0]
    for suite, code in results:
        print(f"  {'OK  ' if code == 0 else 'FAIL'} {suite}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())