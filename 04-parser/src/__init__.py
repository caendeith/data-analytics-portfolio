"""Пакет парсера: загрузка, разбор, нормализация."""
from . import cleaner, fetcher, parser  # noqa: F401

__all__ = ["cleaner", "fetcher", "parser"]