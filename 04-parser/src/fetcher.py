"""Слой загрузки: вежливые запросы, ретраи с бэкоффом, кеш на диске.

Два транспорта:

  * ``requests`` — основной. Для статичного HTML браузер не нужен: это в разы
    быстрее и не тянет 300 МБ Chromium.
  * ``browser`` — резервный, через Playwright (``bin/fetch.mjs``). Нужен, если
    данные рисуются JS, сайт защищается от ботов или окружение пускает трафик
    только через браузер.

Сбор и разбор разделены кешем на диске: парсер читает готовый HTML и вообще не
ходит в сеть. Поэтому разбор тестируется офлайн, а повторный запуск бесплатен.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://books.toscrape.com/"
USER_AGENT = "portfolio-demo-scraper/1.0 (books.toscrape.com sandbox; contact: portfolio)"
RETRY_STATUSES = {429, 500, 502, 503, 504}
NODE_SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "fetch.mjs"


class FetchError(RuntimeError):
    """Не удалось получить страницу после всех попыток."""


def cache_name(url: str) -> str:
    """Имя файла в кеше. Должно совпадать с логикой в bin/fetch.mjs."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".html"


class Fetcher:
    def __init__(
        self,
        delay: float = 0.25,
        retries: int = 3,
        timeout: float = 15.0,
        cache_dir: Path | None = None,
        backend: str = "requests",
    ) -> None:
        if backend not in ("requests", "browser", "auto"):
            raise ValueError(f"неизвестный backend: {backend}")
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.cache_dir = cache_dir
        self.backend = backend
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
        self._last_request_at = 0.0
        self.stats = {"hits": 0, "network": 0, "retries": 0, "browser": 0}

        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ cache
    def cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / cache_name(url)

    def has(self, url: str) -> bool:
        path = self.cache_path(url)
        return bool(path and path.exists() and path.stat().st_size > 0)

    def read(self, url: str) -> str:
        path = self.cache_path(url)
        if path is None or not path.exists():
            raise FetchError(f"нет в кеше: {url}")
        return path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ requests
    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)

    def _get_http(self, url: str) -> str:
        cached = self.cache_path(url)
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                response = self._session.get(url, timeout=self.timeout)
                self._last_request_at = time.monotonic()
                self.stats["network"] += 1

                if response.status_code == 200:
                    response.encoding = "utf-8"
                    text = response.text
                    if cached is not None:
                        cached.write_text(text, encoding="utf-8")
                    return text

                if response.status_code in RETRY_STATUSES:
                    raise requests.HTTPError(f"HTTP {response.status_code}")
                response.raise_for_status()
                return response.text

            except requests.RequestException as exc:
                last_error = exc
                if attempt == self.retries:
                    break
                self.stats["retries"] += 1
                backoff = min(2 ** attempt + random.random(), 30.0)
                log.warning("попытка %s/%s для %s не удалась (%s), пауза %.1f с",
                            attempt, self.retries, url, exc, backoff)
                time.sleep(backoff)

        raise FetchError(f"не удалось получить {url}: {last_error}")

    # ------------------------------------------------------------------ browser
    def _fetch_browser(self, urls: list[str]) -> dict[str, str]:
        """Скачивает пачку URL одним запуском Playwright и пишет в кеш."""
        if self.cache_dir is None:
            raise FetchError("browser-бэкенд требует cache_dir")

        pending = [u for u in urls if not self.has(u)]
        if not pending:
            return {}
        if not NODE_SCRIPT.exists():
            raise FetchError(f"нет скрипта {NODE_SCRIPT}")

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(pending))
            list_path = fh.name

        cmd = [
            "node", str(NODE_SCRIPT),
            "--cache-dir", str(self.cache_dir),
            "--urls-file", list_path,
            "--delay", str(self.delay),
        ]
        log.info("браузерный бэкенд: %s URL", len(pending))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout + 60 * len(pending))

        Path(list_path).unlink(missing_ok=True)

        if proc.returncode != 0:
            lines = (proc.stderr or proc.stdout or "").strip().splitlines()
            interesting = [ln for ln in lines if "Error" in ln or "установите" in ln
                           or "не удалось" in ln or "Cannot find" in ln]
            tail = (interesting or lines)[-4:]
            raise FetchError(
                "браузерный бэкенд упал: " + " | ".join(tail)
                + "\n  установите: npm i playwright && npx playwright install chromium"
                + "\n  или укажите уже установленный модуль:"
                  " PLAYWRIGHT_MODULE=file:///путь/к/node_modules/playwright/index.mjs"
            )

        try:
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            raise FetchError(f"не разобрал ответ fetch.mjs: {proc.stdout[:300]!r}")

        self.stats["browser"] += int(payload.get("fetched", 0))
        failed = payload.get("failed", [])
        if failed:
            log.warning("браузерный бэкенд не осилил %s URL", len(failed))
        return {f["url"]: f.get("error", "") for f in failed}

    # ------------------------------------------------------------------ api
    def ensure(self, urls: list[str], force_browser: bool = False) -> None:
        """Гарантирует, что все URL лежат в кеше.

        Сначала берём из кеша, остальное качаем выбранным транспортом. Если
        ``backend="auto"`` и HTTP упёрся в ошибку на первом же URL — переключаемся
        на браузер и добираем остальное им.
        """
        unique = list(dict.fromkeys(urls))
        for url in unique:
            if self.has(url):
                self.stats["hits"] += 1

        pending = [u for u in unique if not self.has(u)]
        if not pending:
            return

        backend = "browser" if force_browser else self.backend

        if backend in ("requests", "auto"):
            try:
                for url in pending:
                    self._get_http(url)
                return
            except FetchError as exc:
                if backend == "requests":
                    raise
                log.warning("HTTP-транспорт недоступен (%s), переключаюсь на браузер", exc)
                backend = "browser"

        if backend == "browser":
            still_missing = [u for u in pending if not self.has(u)]
            if still_missing:
                self._fetch_browser(still_missing)
            for url in pending:
                if not self.has(url):
                    raise FetchError(f"URL так и не скачался: {url}")

    def get(self, url: str) -> str:
        """Одна страница: из кеша или из сети."""
        if self.has(url):
            self.stats["hits"] += 1
            return self.read(url)
        self.ensure([url])
        return self.read(url)


def absolute_url(href: str, page_url: str | None = None) -> str:
    """Приводит относительную ссылку к абсолютной относительно страницы, где она лежит.

    Правило «добавь catalogue/» здесь не работает: первая страница каталога живёт
    в корне и отдаёт ссылки вида ``catalogue/book_1000/index.html``, а страницы
    2+ живут в ``/catalogue/`` и отдают ``book_999/index.html``. Поэтому
    разрешаем через ``urljoin`` от URL самой страницы — это корректно для всех
    вариантов, включая ``../../media/...`` у картинок.
    """
    href = href.strip()
    if not href:
        return BASE_URL
    return urljoin(page_url or (BASE_URL + "index.html"), href)