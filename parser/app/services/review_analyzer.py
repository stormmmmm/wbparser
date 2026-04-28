from __future__ import annotations

from collections import Counter
from typing import Any

from app.config import Settings
from app.utils.text import normalize_title


class ReviewAnalyzerService:
    def __init__(self, settings: Settings):
        stop_words = settings.load_yaml_config("stop_words.yml", {})
        self.risk_keywords = [str(v).lower() for v in stop_words.get("risk_review_keywords", [])]

    def analyze(self, article_id: str, reviews: list[dict[str, Any]] | list[str]) -> dict[str, Any]:
        normalized_reviews: list[str] = []
        for review in reviews:
            if isinstance(review, dict):
                text = str(review.get("text") or review.get("content") or "")
            else:
                text = str(review)
            cleaned = normalize_title(text)
            if cleaned:
                normalized_reviews.append(cleaned)

        total = len(normalized_reviews)
        if total == 0:
            return {
                "article_id": article_id,
                "positives": [],
                "negatives": [],
                "bad_review_rate": 0.0,
                "main_complaints": [],
                "main_praises": [],
                "quality_notes": None,
                "reject_reason": None,
            }

        bad_matches = []
        for text in normalized_reviews:
            for keyword in self.risk_keywords:
                if keyword in text:
                    bad_matches.append(keyword)

        bad_counter = Counter(bad_matches)
        bad_rate = len(bad_matches) / total if total else 0.0
        complaints = [item for item, _ in bad_counter.most_common(5)]

        reject_reason = "too_many_negative_reviews" if bad_rate >= 0.25 else None
        quality_notes = "есть частые жалобы в отзывах" if complaints else "отзывы без выраженных рисков"

        return {
            "article_id": article_id,
            "positives": [],
            "negatives": complaints,
            "bad_review_rate": round(bad_rate, 4),
            "main_complaints": complaints,
            "main_praises": [],
            "quality_notes": quality_notes,
            "reject_reason": reject_reason,
        }
