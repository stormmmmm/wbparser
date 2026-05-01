"""Daily content planner — turns ready posts into a fixed slot schedule.

Reads the slot grid passed in from ``wbpost`` (which itself comes from
``admin.yml``) and stamps a ``planned_at`` timestamp on each ``ready`` post
so that bridge will only pick it up once its slot has arrived.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone

try:  # py>=3.9
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Post
from app.db.repositories import Repository
from app.utils.time import utcnow

DEFAULT_TIMEZONE = "Europe/Moscow"


@dataclass(frozen=True)
class SlotSpec:
    time: str  # "HH:MM"
    type: str  # "single" | "collection"
    with_reaction_poll: bool = False


@dataclass
class SlotPlan:
    slot: SlotSpec
    planned_at: datetime
    post_id: str | None = None


@dataclass
class DayPlan:
    date: date_type
    timezone: str
    slots: list[SlotPlan] = field(default_factory=list)
    skipped_slots: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "timezone": self.timezone,
            "slots": [
                {
                    "time": s.slot.time,
                    "type": s.slot.type,
                    "with_reaction_poll": s.slot.with_reaction_poll,
                    "planned_at": s.planned_at.isoformat(),
                    "post_id": s.post_id,
                }
                for s in self.slots
            ],
            "skipped_slots": self.skipped_slots,
        }


def _zone(name: str) -> "ZoneInfo | timezone":
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE) if name != DEFAULT_TIMEZONE else timezone.utc


def _slot_to_utc(target_date: date_type, slot_time: str, tz_name: str) -> datetime:
    hh, mm = (int(part) for part in slot_time.split(":"))
    local = datetime.combine(target_date, time(hh, mm), tzinfo=_zone(tz_name))
    return local.astimezone(timezone.utc)


def _normalize_minute_spread(minute_spread: tuple[int, int]) -> tuple[int, int]:
    left, right = int(minute_spread[0]), int(minute_spread[1])
    if left < 0 or right < 0:
        raise ValueError("minute_spread values must be >= 0")
    if left > right:
        raise ValueError("minute_spread min cannot be greater than max")
    if right > 59:
        raise ValueError("minute_spread max cannot exceed 59")
    return (left, right)


def _deterministic_spread_minutes(
    *,
    target_date: date_type,
    slot: SlotSpec,
    tz_name: str,
    minute_spread: tuple[int, int],
    slot_index: int,
) -> int:
    left, right = _normalize_minute_spread(minute_spread)
    if left == right:
        return left
    seed_source = (
        f"{target_date.isoformat()}|{slot_index}|{slot.time}|{slot.type}|"
        f"{int(slot.with_reaction_poll)}|{tz_name}|{left}|{right}"
    )
    digest = hashlib.sha256(seed_source.encode("utf-8")).digest()
    span = right - left + 1
    return left + (int.from_bytes(digest[:8], "big") % span)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_datetime(value: datetime) -> datetime:
    return _as_utc(value).replace(tzinfo=None)


class DailyPlannerService:
    """Stateless service that assigns ``planned_at`` for tomorrow (or any date)."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def plan_day(
        self,
        session: Session,
        slots: list[SlotSpec],
        target_date: date_type | None = None,
        tz_name: str = DEFAULT_TIMEZONE,
        minute_spread: tuple[int, int] = (0, 0),
    ) -> DayPlan:
        if not slots:
            raise ValueError("slots must be non-empty")
        minute_spread = _normalize_minute_spread(minute_spread)

        if target_date is None:
            now_local = utcnow().astimezone(_zone(tz_name))
            target_date = (now_local + timedelta(days=1)).date()

        plan = DayPlan(date=target_date, timezone=tz_name)

        # Fetch enough unplanned posts of each type up-front to satisfy all slots.
        needs_collection = sum(1 for s in slots if s.type == "collection")
        needs_single = sum(1 for s in slots if s.type == "single")
        collections = list(
            Repository.list_unplanned_ready_posts(
                session, post_type="collection", limit=max(needs_collection * 2, 5)
            )
        )
        singles = list(
            Repository.list_unplanned_ready_posts(
                session, post_type="single", limit=max(needs_single * 2, 5)
            )
        )
        # newest first by created_at desc → most recent freshly built post wins
        collections.sort(key=lambda p: p.created_at, reverse=True)
        singles.sort(key=lambda p: p.created_at, reverse=True)

        # Track posts already chosen for this run to avoid double-assignment.
        used_ids: set[str] = set()

        for slot_index, slot in enumerate(slots):
            slot_at_utc = _slot_to_utc(target_date, slot.time, tz_name)
            spread_minutes = _deterministic_spread_minutes(
                target_date=target_date,
                slot=slot,
                tz_name=tz_name,
                minute_spread=minute_spread,
                slot_index=slot_index,
            )
            slot_at_utc = slot_at_utc + timedelta(minutes=spread_minutes)
            slot_plan = SlotPlan(slot=slot, planned_at=slot_at_utc)
            existing = session.scalar(
                select(Post)
                .where(Post.post_type == slot.type)
                .where(Post.planned_at == _db_datetime(slot_at_utc))
                .where(Post.publication_status.in_(("ready", "locked", "published")))
                .order_by(Post.created_at.asc())
                .limit(1)
            )
            if existing is not None:
                slot_plan.post_id = existing.id
                used_ids.add(existing.id)
                plan.slots.append(slot_plan)
                continue

            pool = collections if slot.type == "collection" else singles

            chosen: Post | None = None
            for candidate in pool:
                if candidate.id in used_ids:
                    continue
                chosen = candidate
                break

            if chosen is None:
                plan.skipped_slots.append(
                    {
                        "time": slot.time,
                        "type": slot.type,
                        "reason": f"no unplanned ready {slot.type} posts available",
                    }
                )
                continue

            Repository.assign_planned_at(session, chosen.id, slot_at_utc)
            fresh_until = slot_at_utc + timedelta(minutes=self.settings.POST_REVALIDATE_MINUTES)
            if _as_utc(chosen.fresh_until) < fresh_until:
                chosen.fresh_until = fresh_until
            slot_plan.post_id = chosen.id
            used_ids.add(chosen.id)
            plan.slots.append(slot_plan)

        return plan
