from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.utils.time import utcnow


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    title: Mapped[str] = mapped_column(String(512))
    normalized_title: Mapped[str] = mapped_column(String(512), index=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)

    product_url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    affiliate_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    main_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_urls: Mapped[list[str]] = mapped_column(JSON, default=list)

    current_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    old_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedbacks_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    orders_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    popularity_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    availability: Mapped[bool] = mapped_column(Boolean, default=True)
    sizes: Mapped[list[str]] = mapped_column(JSON, default=list)
    colors: Mapped[list[str]] = mapped_column(JSON, default=list)

    seller_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(32), default="search", index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    snapshots: Mapped[list["ProductSnapshot"]] = relationship(back_populates="product")


class ProductSnapshot(Base):
    __tablename__ = "product_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("products.article_id"), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    old_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedbacks_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    availability: Mapped[bool] = mapped_column(Boolean, default=True)
    category_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    product: Mapped[Product] = relationship(back_populates="snapshots")


class SelectedCandidate(Base):
    __tablename__ = "selected_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("products.article_id"), index=True)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    selection_score: Mapped[float] = mapped_column(Float, index=True)
    visual_score: Mapped[float] = mapped_column(Float)
    audience_fit_score: Mapped[float] = mapped_column(Float)
    price_score: Mapped[float] = mapped_column(Float)
    discount_score: Mapped[float] = mapped_column(Float)
    rating_score: Mapped[float] = mapped_column(Float)
    feedback_score: Mapped[float] = mapped_column(Float)
    trend_score: Mapped[float] = mapped_column(Float)
    season_score: Mapped[float] = mapped_column(Float)
    risk_penalty: Mapped[float] = mapped_column(Float, default=0)

    reason_for_selection: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_flags: Mapped[list[str]] = mapped_column(JSON, default=list)


class ReviewSummary(Base):
    __tablename__ = "review_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("products.article_id"), index=True)
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    positives: Mapped[list[str]] = mapped_column(JSON, default=list)
    negatives: Mapped[list[str]] = mapped_column(JSON, default=list)
    bad_review_rate: Mapped[float] = mapped_column(Float, default=0.0)
    main_complaints: Mapped[list[str]] = mapped_column(JSON, default=list)
    main_praises: Mapped[list[str]] = mapped_column(JSON, default=list)
    quality_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("products.article_id"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[str] = mapped_column(String(32), default="photo")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_main: Mapped[bool] = mapped_column(Boolean, default=False)
    visual_rank: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    post_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    post_text: Mapped[str] = mapped_column(Text)
    media_urls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fresh_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    publication_status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    lock_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lock_worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    telegram_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    telegram_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    reactions_total: Mapped[int] = mapped_column(Integer, default=0)
    reaction_breakdown: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    views_count: Mapped[int] = mapped_column(Integer, default=0)
    clicks_count: Mapped[int] = mapped_column(Integer, default=0)
    conversion_count: Mapped[int] = mapped_column(Integer, default=0)
    parse_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    items: Mapped[list["PostItem"]] = relationship(back_populates="post")


class PostItem(Base):
    __tablename__ = "post_items"
    __table_args__ = (UniqueConstraint("post_id", "article_id", name="uq_post_article"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id"), index=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("products.article_id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=1)
    displayed_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    displayed_old_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    displayed_discount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    displayed_url: Mapped[str] = mapped_column(Text)
    displayed_caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    post: Mapped[Post] = relationship(back_populates="items")


class PublicationMetric(Base):
    __tablename__ = "publication_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id"), index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    views_count: Mapped[int] = mapped_column(Integer, default=0)
    reactions_total: Mapped[int] = mapped_column(Integer, default=0)
    reaction_breakdown: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    comments_count: Mapped[int] = mapped_column(Integer, default=0)
    forwards_count: Mapped[int] = mapped_column(Integer, default=0)


class ClickMetric(Base):
    __tablename__ = "click_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id"), index=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("products.article_id"), index=True)
    clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    click_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    redirect_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CategoryRule(Base):
    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_name: Mapped[str] = mapped_column(String(255), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_price: Mapped[int] = mapped_column(Integer, default=100)
    max_price: Mapped[int] = mapped_column(Integer, default=3000)
    min_rating: Mapped[float] = mapped_column(Float, default=4.6)
    min_feedbacks: Mapped[int] = mapped_column(Integer, default=30)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExcludedArticle(Base):
    __tablename__ = "excluded_articles"

    article_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
