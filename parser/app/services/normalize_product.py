from __future__ import annotations

from typing import Any

from app.schemas.product import ParsedProduct
from app.services.deduplication import dedupe_strings
from app.utils.text import normalize_title
from app.utils.time import utcnow
from app.utils.urls import build_canonical_wb_url, canonicalize_wb_url, extract_article_id


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _price_to_rub(value: Any, from_minor_units: bool = False) -> int | None:
    parsed = _to_int(value)
    if parsed is None:
        return None
    if from_minor_units:
        return max(parsed // 100, 0)
    return parsed


def _wb_basket_number(article_id: int) -> int:
    vol = article_id // 100000
    ranges = [
        (143, 1),
        (287, 2),
        (431, 3),
        (719, 4),
        (1007, 5),
        (1061, 6),
        (1115, 7),
        (1169, 8),
        (1313, 9),
        (1601, 10),
        (1655, 11),
        (1919, 12),
        (2045, 13),
        (2189, 14),
        (2405, 15),
        (2621, 16),
        (2837, 17),
        (3053, 18),
        (3269, 19),
        (3485, 20),
        (3701, 21),
        (3917, 22),
        (4133, 23),
        (4349, 24),
        (4565, 25),
        (4781, 26),
        (4997, 27),
        (5213, 28),
    ]
    for upper, basket in ranges:
        if vol <= upper:
            return basket
    return 29


def _wb_image_urls(article_id: str, pics: Any) -> list[str]:
    count = _to_int(pics)
    if not count:
        return []
    nm = _to_int(article_id)
    if nm is None:
        return []
    basket = _wb_basket_number(nm)
    vol = nm // 100000
    part = nm // 1000
    return [
        f"https://basket-{basket:02d}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/big/{idx}.webp"
        for idx in range(1, count + 1)
    ]


def _extract_images(raw: dict[str, Any]) -> list[str]:
    image_urls: list[str] = []
    for key in ("image_urls", "images", "photos", "photoUrls", "pics"):
        value = raw.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    image_urls.append(item)
                elif isinstance(item, dict):
                    for image_key in ("big", "original", "url", "tm", "c516x688", "c246x328"):
                        if item.get(image_key):
                            image_urls.append(str(item[image_key]))
                            break
        elif key == "pics":
            article_id = extract_article_id(raw.get("id") or raw.get("nmId") or raw.get("article"))
            if article_id:
                image_urls.extend(_wb_image_urls(article_id, value))
    return [url for url in dedupe_strings(image_urls) if url.startswith("http")]


def _extract_sizes(raw: dict[str, Any]) -> list[str]:
    sizes: list[str] = []
    for key in ("sizes", "availableSizes", "sizes_available"):
        value = raw.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str):
                sizes.append(item)
            elif isinstance(item, dict):
                size_name = item.get("name") or item.get("origName") or item.get("optionId")
                if size_name:
                    sizes.append(str(size_name))
    return dedupe_strings(sizes)


def _extract_colors(raw: dict[str, Any]) -> list[str]:
    colors: list[str] = []
    for key in ("colors", "colorNames", "colors_available"):
        value = raw.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str):
                colors.append(item)
            elif isinstance(item, dict):
                color_name = item.get("name") or item.get("value")
                if color_name:
                    colors.append(str(color_name))
    return dedupe_strings(colors)


def normalize_wb_product(
    raw: dict[str, Any],
    source: str,
    source_url: str | None = None,
) -> ParsedProduct | None:
    article_id = extract_article_id(raw.get("id") or raw.get("nmId") or raw.get("article"))
    if article_id is None:
        article_id = extract_article_id(raw.get("link") or raw.get("url"))
    if article_id is None:
        return None

    title = str(raw.get("name") or raw.get("title") or f"Товар {article_id}").strip()
    normalized = normalize_title(title)
    brand = raw.get("brand")
    category_id = raw.get("categoryId") or raw.get("subjectId")
    category_name = raw.get("categoryName") or raw.get("entity")
    subject_name = raw.get("subjectName") or raw.get("subject")

    sale_price_u = raw.get("salePriceU")
    old_price_u = raw.get("priceU")
    sale_price = raw.get("salePrice") if sale_price_u is None else None
    base_price = raw.get("price") if old_price_u is None else None
    if sale_price_u is None and old_price_u is None:
        sizes = raw.get("sizes")
        if isinstance(sizes, list) and sizes:
            size_price = sizes[0].get("price") if isinstance(sizes[0], dict) else None
            if isinstance(size_price, dict):
                sale_price_u = size_price.get("product")
                old_price_u = size_price.get("basic")

    price = _price_to_rub(sale_price_u, from_minor_units=True) if sale_price_u else _price_to_rub(sale_price)
    old_price = _price_to_rub(old_price_u, from_minor_units=True) if old_price_u else _price_to_rub(base_price)
    if price is None:
        price = _to_int(raw.get("salePriceRUB")) or _to_int(raw.get("finalPrice"))
    if old_price is None:
        old_price = _to_int(raw.get("basePrice"))

    discount_percent = _to_int(raw.get("sale"))
    if discount_percent is None and price and old_price and old_price > price:
        discount_percent = int(round((old_price - price) / old_price * 100))

    rating = raw.get("reviewRating") or raw.get("rating")
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None

    feedbacks = (
        _to_int(raw.get("feedbacks"))
        or _to_int(raw.get("feedbacksCount"))
        or _to_int(raw.get("commentsCount"))
    )
    orders_count = _to_int(raw.get("ordersCount") or raw.get("orders") or raw.get("saleCount"))
    popularity_score = float(orders_count) if orders_count is not None else None

    images = _extract_images(raw)
    main_image = images[0] if images else raw.get("mainImage")

    url = raw.get("link") or raw.get("url") or source_url or build_canonical_wb_url(article_id)
    canonical_url = canonicalize_wb_url(url) or build_canonical_wb_url(article_id)

    quantity_source = raw.get("totalQuantity")
    if quantity_source is None:
        quantity_source = raw.get("quantity")
    quantity = _to_int(quantity_source)
    availability = bool(raw.get("available", True))
    if quantity is not None:
        availability = quantity > 0

    sizes = _extract_sizes(raw)
    if not sizes and raw.get("sizesCount"):
        sizes = [f"size_{i+1}" for i in range(int(raw["sizesCount"]))]
    colors = _extract_colors(raw)

    now = utcnow()
    tags = dedupe_strings(
        [
            token
            for token in [category_name, subject_name, brand]
            if token and isinstance(token, str)
        ]
    )

    return ParsedProduct(
        article_id=article_id,
        title=title,
        normalized_title=normalized,
        brand=brand if isinstance(brand, str) else None,
        category_id=str(category_id) if category_id is not None else None,
        category_name=category_name if isinstance(category_name, str) else None,
        subject_name=subject_name if isinstance(subject_name, str) else None,
        tags=tags,
        price=price,
        old_price=old_price,
        discount_percent=discount_percent,
        rating=rating,
        feedbacks_count=feedbacks,
        orders_count=orders_count,
        popularity_score=popularity_score,
        main_image_url=main_image,
        image_urls=images,
        product_url=str(url),
        canonical_url=canonical_url,
        availability=availability,
        sizes_available=sizes,
        colors_available=colors,
        seller_id=str(raw.get("supplierId")) if raw.get("supplierId") else None,
        seller_name=raw.get("supplier") if isinstance(raw.get("supplier"), str) else None,
        source=source,
        source_url=source_url,
        detected_at=now,
        first_seen_at=now,
        last_checked_at=now,
    )
