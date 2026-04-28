from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings
from app.db.repositories import Repository
from app.schemas.product import ProductScoreResult
from app.services.normalize_product import normalize_wb_product
from app.services.post_builder import PostBuilderService


def test_post_builder_creates_single_post(db_session):
    settings = get_settings()
    raw = json.loads((Path(__file__).parent / "fixtures" / "wb_product.json").read_text(encoding="utf-8"))
    product = normalize_wb_product(raw, source="search")
    Repository.upsert_product(db_session, product)

    score = ProductScoreResult(
        article_id=product.article_id,
        selection_score=82,
        visual_score=90,
        audience_fit_score=88,
        price_score=90,
        discount_score=80,
        rating_score=90,
        feedback_score=75,
        trend_score=70,
        season_score=60,
        risk_penalty=0,
        status="ready",
        reason_for_selection="визуально сильный товар до 1000 ₽",
    )
    Repository.save_candidate_score(db_session, product.article_id, score, risk_flags=[])

    builder = PostBuilderService(settings)
    created = builder.build_posts(db_session, post_type="single", limit=1)

    assert len(created) == 1
    post = Repository.get_post(db_session, created[0])
    assert post is not None
    assert post.publication_status == "ready"
    assert product.article_id in post.post_text
