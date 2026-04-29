from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.repositories import Repository
from app.schemas.product import ParsedProduct, ProductScoreResult
from app.utils.text import contains_any


@dataclass
class ScoreWeights:
    visual: float
    audience_fit: float
    price: float
    discount: float
    rating: float
    feedback: float
    trend: float
    season: float


class ScoringService:
    def __init__(self, settings: Settings):
        self.settings = settings
        scoring = settings.load_yaml_config("scoring.yml", {})
        weights = scoring.get("weights", {})
        self.weights = ScoreWeights(
            visual=float(weights.get("visual_score", 0.30)),
            audience_fit=float(weights.get("audience_fit_score", 0.20)),
            price=float(weights.get("price_score", 0.15)),
            discount=float(weights.get("discount_score", 0.10)),
            rating=float(weights.get("rating_score", 0.10)),
            feedback=float(weights.get("feedback_score", 0.05)),
            trend=float(weights.get("trend_score", 0.05)),
            season=float(weights.get("season_score", 0.05)),
        )
        thresholds = scoring.get("thresholds", {})
        self.ready_threshold = float(thresholds.get("ready", 70))
        self.review_threshold = float(thresholds.get("needs_review", 55))
        self.penalty_map = scoring.get("risk_penalty", {})

        trends = settings.load_yaml_config("trend_keywords.yml", {})
        self.trend_keywords = [str(v).lower() for v in trends.get("trend_keywords", [])]
        self.season_keywords = {
            key: [str(v).lower() for v in values] for key, values in trends.get("season_keywords", {}).items()
        }

    def _visual_score(self, product: ParsedProduct) -> float:
        score = 20.0
        image_count = len(product.image_urls)
        score += min(40.0, image_count * 8.0)
        if product.main_image_url:
            score += 10
        if contains_any(product.normalized_title, ("эстет", "мил", "уют", "korean", "y2k", "pinterest")):
            score += 20
        return min(100.0, score)

    def _audience_fit_score(self, product: ParsedProduct) -> float:
        text = " ".join(
            filter(
                None,
                [
                    product.normalized_title,
                    product.category_name or "",
                    product.subject_name or "",
                    " ".join(product.tags),
                ],
            )
        )
        if contains_any(
            text,
            (
                "жен",
                "бель",
                "космет",
                "уход",
                "сумк",
                "аксессуар",
                "декор",
                "подар",
                "канцел",
                "комнат",
                "уют",
                "подрост",
                "краб",
                "закол",
                "шапк",
                "y2k",
                "у2к",
            ),
        ):
            return 90.0
        return 45.0

    def _price_score(self, product: ParsedProduct) -> float:
        if product.price is None:
            return 0.0
        price = product.price
        if 300 <= price <= 1500:
            return 95.0
        if 100 <= price < 300:
            return 75.0
        if 1500 < price <= 3000:
            return 70.0
        if 3000 < price <= 5000:
            return 45.0
        return 20.0

    def _discount_score(self, product: ParsedProduct) -> float:
        if product.discount_percent is None:
            return 30.0
        if product.discount_percent < 5:
            return 20.0
        if product.discount_percent < 20:
            return 55.0
        if product.discount_percent <= 70:
            return 85.0
        return 50.0

    def _rating_score(self, product: ParsedProduct) -> float:
        if product.rating is None:
            return 0.0
        rating = product.rating
        if rating >= 4.9:
            return 100.0
        if rating >= 4.8:
            return 95.0
        if rating >= 4.6:
            return 85.0
        if rating >= 4.5:
            return 65.0
        return 20.0

    def _feedback_score(self, product: ParsedProduct) -> float:
        if product.feedbacks_count is None:
            return 0.0
        count = product.feedbacks_count
        if count >= 500:
            return 100.0
        if count >= 200:
            return 90.0
        if count >= 100:
            return 80.0
        if count >= 50:
            return 65.0
        if count >= 20:
            return 50.0
        return 20.0

    def _trend_score(self, product: ParsedProduct) -> float:
        text = f"{product.normalized_title} {' '.join(product.tags)}".lower()
        matches = sum(1 for keyword in self.trend_keywords if keyword in text)
        return min(100.0, 40.0 + matches * 15.0) if matches else 20.0

    def _season_name(self, now: datetime) -> str:
        month = now.month
        if month in (12, 1, 2):
            return "winter"
        if month in (3, 4, 5):
            return "spring"
        if month in (6, 7, 8):
            return "summer"
        return "autumn"

    def _season_score(self, product: ParsedProduct, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        season = self._season_name(now)
        keywords = self.season_keywords.get(season, [])
        text = f"{product.normalized_title} {product.category_name or ''} {product.subject_name or ''}".lower()
        matches = sum(1 for kw in keywords if kw in text)
        return min(100.0, 35.0 + matches * 20.0) if matches else 35.0

    def _risk_penalty(self, risk_flags: list[str]) -> float:
        penalty = 0.0
        for flag in risk_flags:
            penalty += float(self.penalty_map.get(flag, 0))
            if "suspicious" in flag:
                penalty += float(self.penalty_map.get("suspicious_discount", 10))
            if "availability" in flag:
                penalty += float(self.penalty_map.get("no_availability", 30))
            if "rating" in flag:
                penalty += float(self.penalty_map.get("low_rating", 25))
            if "feedback" in flag:
                penalty += float(self.penalty_map.get("low_feedbacks", 20))
        return min(80.0, penalty)

    def score(self, product: ParsedProduct, risk_flags: list[str] | None = None) -> ProductScoreResult:
        risk_flags = risk_flags or []
        visual_score = self._visual_score(product)
        audience_fit_score = self._audience_fit_score(product)
        price_score = self._price_score(product)
        discount_score = self._discount_score(product)
        rating_score = self._rating_score(product)
        feedback_score = self._feedback_score(product)
        trend_score = self._trend_score(product)
        season_score = self._season_score(product)
        risk_penalty = self._risk_penalty(risk_flags)

        selection_score = (
            self.weights.visual * visual_score
            + self.weights.audience_fit * audience_fit_score
            + self.weights.price * price_score
            + self.weights.discount * discount_score
            + self.weights.rating * rating_score
            + self.weights.feedback * feedback_score
            + self.weights.trend * trend_score
            + self.weights.season * season_score
            - risk_penalty
        )
        selection_score = max(0.0, min(100.0, selection_score))

        if selection_score >= self.ready_threshold:
            status = "ready"
        elif selection_score >= self.review_threshold:
            status = "needs_review"
        else:
            status = "rejected"

        reason = (
            f"visual={visual_score:.0f}, audience={audience_fit_score:.0f}, "
            f"price={price_score:.0f}, trend={trend_score:.0f}"
        )

        return ProductScoreResult(
            article_id=product.article_id,
            selection_score=round(selection_score, 2),
            visual_score=round(visual_score, 2),
            audience_fit_score=round(audience_fit_score, 2),
            price_score=round(price_score, 2),
            discount_score=round(discount_score, 2),
            rating_score=round(rating_score, 2),
            feedback_score=round(feedback_score, 2),
            trend_score=round(trend_score, 2),
            season_score=round(season_score, 2),
            risk_penalty=round(risk_penalty, 2),
            status=status,
            reason_for_selection=reason,
        )

    def score_products_in_db(self, session: Session, limit: int = 500) -> dict[str, int]:
        products = Repository.list_products_for_scoring(session, limit=limit)
        scored = 0
        rejected = 0
        for product in products:
            parsed = ParsedProduct(
                article_id=product.article_id,
                title=product.title,
                normalized_title=product.normalized_title,
                brand=product.brand,
                category_id=product.category_id,
                category_name=product.category_name,
                subject_name=product.subject_name,
                tags=product.tags or [],
                price=product.current_price,
                old_price=product.old_price,
                discount_percent=product.discount_percent,
                rating=product.rating,
                feedbacks_count=product.feedbacks_count,
                orders_count=product.orders_count,
                popularity_score=product.popularity_score,
                main_image_url=product.main_image_url,
                image_urls=product.image_urls or [],
                product_url=product.product_url,
                canonical_url=product.canonical_url,
                affiliate_url=product.affiliate_url,
                availability=product.availability,
                sizes_available=product.sizes or [],
                colors_available=product.colors or [],
                seller_id=product.seller_id,
                seller_name=product.seller_name,
                source=product.source,
                source_url=None,
                detected_at=product.detected_at,
                first_seen_at=product.first_seen_at,
                last_checked_at=product.last_checked_at,
            )
            risk_flags: list[str] = []
            score_result = self.score(parsed, risk_flags=risk_flags)
            Repository.save_candidate_score(
                session=session,
                article_id=product.article_id,
                score=score_result,
                risk_flags=risk_flags,
            )
            scored += 1
            if score_result.status == "rejected":
                rejected += 1
        return {"scored": scored, "rejected": rejected}
