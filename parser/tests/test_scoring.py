from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings
from app.services.normalize_product import normalize_wb_product
from app.services.score_products import ScoringService


def test_scoring_returns_ready_for_strong_product(test_env):
    settings = get_settings()
    service = ScoringService(settings)
    raw = json.loads((Path(__file__).parent / "fixtures" / "wb_product.json").read_text(encoding="utf-8"))
    product = normalize_wb_product(raw, source="search")

    result = service.score(product, risk_flags=[])

    assert 0 <= result.selection_score <= 100
    assert result.status in {"ready", "needs_review", "rejected"}
    assert result.selection_score >= 70
    assert result.status == "ready"
