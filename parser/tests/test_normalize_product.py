from __future__ import annotations

import json
from pathlib import Path

from app.services.normalize_product import normalize_wb_product


def test_normalize_product_from_fixture():
    fixture_path = Path(__file__).parent / "fixtures" / "wb_product.json"
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    product = normalize_wb_product(raw, source="search", source_url="органайзер")

    assert product is not None
    assert product.article_id == "12345678"
    assert product.price == 699
    assert product.old_price == 1299
    assert product.discount_percent == 46
    assert product.canonical_url == "https://www.wildberries.ru/catalog/12345678/detail.aspx"
    assert len(product.image_urls) >= 3
    assert product.availability is True
