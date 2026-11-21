"""reviewlab — разбор отзывов на маркетплейсах: данные, правила, LLM, метрики."""

from .dataset import LABELS, SEED, Review, generate_reviews

__all__ = ["LABELS", "SEED", "Review", "generate_reviews"]
