"""Indexing configuration for CacheInfinity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class IndexingSettings:
    """Settings for the indexer."""

    min_full_reindex_days: int = 30
    max_full_reindex_days: int = 90
    hot_window_days: int = 7
    hot_radius: int = 10
    daily_full_reindex_budget: int = 5
    daily_cheap_check_budget: int = 10
    max_full_reindex_per_14d: int = 10
    max_cheap_checks_per_day: int = 50
    allow_early_full_on_change: bool = True
    early_full_requires_hot: bool = True
    score_weights: Optional[dict[str, float]] = None