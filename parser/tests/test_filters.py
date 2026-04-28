from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings
from app.services.filter_products import FilterService
from app.services.normalize_product import normalize_wb_product


def test_filter_accepts_good_product(test_env):
    settings = get_settings()
    service = FilterService(settings)
    raw = json.loads((Path(__file__).parent / "fixtures" / "wb_product.json").read_text(encoding="utf-8"))
    product = normalize_wb_product(raw, source="search")
    result = service.evaluate(product)

    assert result.accepted is True
    assert result.hard_reject is False


def test_filter_rejects_low_rating(test_env):
    settings = get_settings()
    service = FilterService(settings)
    raw = json.loads((Path(__file__).parent / "fixtures" / "wb_product.json").read_text(encoding="utf-8"))
    raw["reviewRating"] = 4.2
    product = normalize_wb_product(raw, source="search")
    result = service.evaluate(product)

    assert result.accepted is False
    assert "low_rating" in result.reject_reasons
