from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.repositories import Repository
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/products", tags=["products"])


@router.get("/{article_id}")
def get_product(article_id: str, session: Session = Depends(get_session)) -> dict:
    product = Repository.get_product(session, article_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product_not_found")
    return {
        "article_id": product.article_id,
        "title": product.title,
        "normalized_title": product.normalized_title,
        "brand": product.brand,
        "category_id": product.category_id,
        "category_name": product.category_name,
        "subject_name": product.subject_name,
        "tags": product.tags,
        "price": product.current_price,
        "old_price": product.old_price,
        "discount_percent": product.discount_percent,
        "rating": product.rating,
        "feedbacks_count": product.feedbacks_count,
        "orders_count": product.orders_count,
        "popularity_score": product.popularity_score,
        "main_image_url": product.main_image_url,
        "image_urls": product.image_urls,
        "product_url": product.product_url,
        "canonical_url": product.canonical_url,
        "affiliate_url": product.affiliate_url,
        "availability": product.availability,
        "sizes_available": product.sizes,
        "colors_available": product.colors,
        "seller_id": product.seller_id,
        "seller_name": product.seller_name,
        "source": product.source,
        "detected_at": product.detected_at,
        "first_seen_at": product.first_seen_at,
        "last_checked_at": product.last_checked_at,
    }
