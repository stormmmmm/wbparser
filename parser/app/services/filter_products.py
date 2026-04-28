from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.schemas.product import ParsedProduct, ProductFilterResult
from app.utils.text import contains_any


@dataclass
class FilterConfig:
    min_images: int
    min_rating_default: float
    min_rating_strict: float
    min_feedbacks_default: int
    min_feedbacks_beauty: int
    min_feedbacks_tech: int
    absolute_min_price: int
    absolute_max_price: int
    premium_max_price: int


class FilterService:
    STRICT_RATING_CATEGORIES = ("космет", "уход", "белье", "техника", "бад")

    def __init__(self, settings: Settings):
        self.settings = settings
        scoring = settings.load_yaml_config("scoring.yml", {})
        filters = scoring.get("filters", {})
        price_ranges = scoring.get("price_ranges", {})
        self.config = FilterConfig(
            min_images=int(filters.get("min_images", 3)),
            min_rating_default=float(filters.get("min_rating_default", 4.6)),
            min_rating_strict=float(filters.get("min_rating_strict", 4.8)),
            min_feedbacks_default=int(filters.get("min_feedbacks_default", 30)),
            min_feedbacks_beauty=int(filters.get("min_feedbacks_beauty", 50)),
            min_feedbacks_tech=int(filters.get("min_feedbacks_tech", 100)),
            absolute_min_price=int(price_ranges.get("absolute_min", 100)),
            absolute_max_price=int(price_ranges.get("absolute_max", 3000)),
            premium_max_price=int(price_ranges.get("premium_max", 10000)),
        )
        stop_words = settings.load_yaml_config("stop_words.yml", {})
        self.blocked_categories = [str(v).lower() for v in stop_words.get("blocked_categories", [])]
        self.blocked_keywords = [str(v).lower() for v in stop_words.get("blocked_keywords", [])]

        excluded_cfg = settings.load_yaml_config("excluded_articles.yml", {})
        self.excluded_articles = {
            str(item.get("article_id"))
            for item in excluded_cfg.get("excluded_articles", [])
            if item.get("article_id")
        }

    def evaluate(self, product: ParsedProduct, premium_mode: bool = False) -> ProductFilterResult:
        risk_flags: list[str] = []
        reject_reasons: list[str] = []

        if product.article_id in self.excluded_articles:
            reject_reasons.append("article_excluded")

        category_text = f"{product.category_name or ''} {product.subject_name or ''}".lower()
        title_text = product.normalized_title.lower()

        if contains_any(category_text, self.blocked_categories) or contains_any(
            title_text, self.blocked_keywords
        ):
            reject_reasons.append("blocked_category_or_keyword")

        if not product.availability:
            reject_reasons.append("unavailable")
            risk_flags.append("availability_false")

        if product.price is None:
            reject_reasons.append("missing_price")
        else:
            max_price = self.config.premium_max_price if premium_mode else self.config.absolute_max_price
            if product.price < self.config.absolute_min_price or product.price > max_price:
                reject_reasons.append("price_out_of_range")

        strict_category = contains_any(
            f"{title_text} {category_text}",
            self.STRICT_RATING_CATEGORIES,
        )
        min_rating = self.config.min_rating_strict if strict_category else self.config.min_rating_default
        if product.rating is None or product.rating < min_rating:
            reject_reasons.append("low_rating")
            risk_flags.append("rating_below_threshold")

        min_feedbacks = self.config.min_feedbacks_default
        if contains_any(f"{title_text} {category_text}", ("космет", "уход", "бьюти")):
            min_feedbacks = self.config.min_feedbacks_beauty
        if contains_any(f"{title_text} {category_text}", ("техника", "гаджет", "бад")):
            min_feedbacks = self.config.min_feedbacks_tech

        if product.feedbacks_count is None or product.feedbacks_count < min_feedbacks:
            if product.price and product.price <= 700 and len(product.image_urls) >= 5:
                risk_flags.append("low_feedbacks_allowed_for_impulse")
            else:
                reject_reasons.append("low_feedbacks")
                risk_flags.append("feedbacks_below_threshold")

        if len(product.image_urls) < self.config.min_images:
            reject_reasons.append("not_enough_images")
            risk_flags.append("weak_visual")

        if product.old_price and product.price and product.old_price <= product.price:
            risk_flags.append("suspicious_old_price")

        hard_reject = len(reject_reasons) > 0
        return ProductFilterResult(
            accepted=not hard_reject,
            hard_reject=hard_reject,
            risk_flags=sorted(set(risk_flags)),
            reject_reasons=sorted(set(reject_reasons)),
        )
