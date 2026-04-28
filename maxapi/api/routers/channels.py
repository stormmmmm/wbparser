"""/v1/accounts/{accountId}/channels/* endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from api.backends import UpstreamChannel
from api.deps import AuthDep, BackendDep, CursorDep, LimitDep, StorageDep
from api.errors import ConflictError, ForbiddenError, NotFoundError
from api.models.channels import (
    Channel,
    ChannelListResponse,
    ChannelPermissions,
    ChannelRole,
)
from api.pagination import paginate

router = APIRouter(prefix="/v1/accounts/{account_id}/channels", tags=["channels"])


def _to_channel(upstream: UpstreamChannel) -> Channel:
    return Channel(
        channel_id=upstream.channel_id,
        title=upstream.title,
        username=upstream.username,
        public_link=upstream.public_link,
        type=upstream.type,
        role=upstream.role,
        members_count=upstream.members_count,
        permissions=upstream.permissions,
    )


@router.get(
    "",
    summary="List channels visible to account",
    response_model=ChannelListResponse,
    operation_id="listChannels",
)
async def list_channels(
    account_id: str,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
    limit: LimitDep,
    cursor: CursorDep,
    only_writable: Annotated[bool, Query(description="Return only writable channels.")] = True,
    role: Annotated[ChannelRole | None, Query(description="Filter by role.")] = None,
    title: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=256,
            description=(
                "Filter by human-readable channel title. Matching behaviour "
                "is controlled by `title_match` and `case_insensitive`."
            ),
        ),
    ] = None,
    title_match: Annotated[
        str,
        Query(
            description="How `title` should match: `exact` (default) or `contains`.",
            pattern="^(exact|contains)$",
        ),
    ] = "exact",
    case_insensitive: Annotated[
        bool,
        Query(
            description="Apply a case-insensitive comparison when filtering by `title`.",
        ),
    ] = True,
) -> ChannelListResponse:
    storage.get_account(account_id)
    upstream_channels = await backend.list_channels(account_id=account_id)
    channels: list[Channel] = []
    for upstream in upstream_channels:
        channels.append(storage.upsert_channel(account_id, _to_channel(upstream)))
    if only_writable:
        channels = [c for c in channels if c.permissions.can_publish]
    if role is not None:
        channels = [c for c in channels if c.role == role]
    if title is not None:
        channels = _filter_channels_by_title(
            channels, title=title, match=title_match, case_insensitive=case_insensitive
        )
    items, next_cursor = paginate(channels, cursor, limit)
    return ChannelListResponse(items=items, next_cursor=next_cursor)


@router.get(
    "/find",
    summary="Find a single channel by its human-readable title",
    response_model=Channel,
    operation_id="findChannelByTitle",
)
async def find_channel_by_title(
    account_id: str,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
    title: Annotated[
        str,
        Query(
            min_length=1,
            max_length=256,
            description="Human-readable channel title (as shown in the MAX app).",
        ),
    ],
    title_match: Annotated[
        str,
        Query(
            description="How to match: `exact` (default) or `contains`.",
            pattern="^(exact|contains)$",
        ),
    ] = "exact",
    case_insensitive: Annotated[
        bool,
        Query(description="Apply a case-insensitive comparison. Defaults to true."),
    ] = True,
    only_writable: Annotated[
        bool,
        Query(description="Limit search to channels the account can publish to."),
    ] = False,
) -> Channel:
    """Return the single channel whose title matches.

    Raises 404 when nothing matches and 409 when more than one channel
    matches the criteria — in that case, refine with ``title_match=exact``
    or disambiguate via ``/channels?title=...``.
    """
    storage.get_account(account_id)
    upstream_channels = await backend.list_channels(account_id=account_id)
    candidates: list[Channel] = []
    for upstream in upstream_channels:
        candidates.append(storage.upsert_channel(account_id, _to_channel(upstream)))
    if only_writable:
        candidates = [c for c in candidates if c.permissions.can_publish]
    matched = _filter_channels_by_title(
        candidates, title=title, match=title_match, case_insensitive=case_insensitive
    )
    if not matched:
        raise NotFoundError(
            f"No channel found with title={title!r}.", code="channel_not_found"
        )
    if len(matched) > 1:
        raise ConflictError(
            f"Found {len(matched)} channels matching title={title!r}; "
            "refine the query with title_match=exact or pick one via /channels?title=...",
            code="channel_title_ambiguous",
        )
    return matched[0]


def _filter_channels_by_title(
    channels: list[Channel],
    *,
    title: str,
    match: str,
    case_insensitive: bool,
) -> list[Channel]:
    needle = title.strip()
    if case_insensitive:
        needle_cmp = needle.casefold()
    else:
        needle_cmp = needle

    def _match(channel: Channel) -> bool:
        haystack = channel.title or ""
        hay = haystack.casefold() if case_insensitive else haystack
        if match == "contains":
            return needle_cmp in hay
        return hay == needle_cmp

    return [c for c in channels if _match(c)]


@router.get(
    "/resolve",
    summary="Resolve channel by public link or username",
    response_model=Channel,
    operation_id="resolveChannel",
)
async def resolve_channel(
    account_id: str,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
    link: Annotated[str, Query(min_length=2, description="Public link, username, or slug.")],
) -> Channel:
    storage.get_account(account_id)
    upstream = await backend.resolve_channel(account_id=account_id, link=link)
    if upstream is None:
        raise NotFoundError(
            "Channel could not be resolved.", code="channel_not_resolved"
        )
    channel = storage.upsert_channel(account_id, _to_channel(upstream))
    if not channel.permissions.can_publish and channel.role == ChannelRole.member:
        raise ForbiddenError("Account cannot publish to this channel.")
    return channel


@router.get(
    "/{channel_id}",
    summary="Get channel",
    response_model=Channel,
    operation_id="getChannel",
)
def get_channel(
    account_id: str,
    channel_id: str,
    storage: StorageDep,
    _auth: AuthDep,
) -> Channel:
    return storage.get_channel(account_id, channel_id)


@router.get(
    "/{channel_id}/permissions",
    summary="Get posting permissions",
    response_model=ChannelPermissions,
    operation_id="getChannelPermissions",
)
def get_channel_permissions(
    account_id: str,
    channel_id: str,
    storage: StorageDep,
    _auth: AuthDep,
) -> ChannelPermissions:
    channel = storage.get_channel(account_id, channel_id)
    return channel.permissions


__all__ = ["router"]
