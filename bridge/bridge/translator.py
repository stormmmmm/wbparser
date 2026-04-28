"""Translate parser ``ReadyPost`` JSON into MAX gateway request bodies.

The parser and the gateway agree on the shape of ``ReadyPost``, but a few
fields are intentionally laxer on the parser side (free-form strings) and
stricter on the gateway side (enums). This module performs the *only*
mapping the bridge is allowed to do — never any business logic.

Both inputs and outputs are plain ``dict`` objects so the translator is
trivially testable without importing FastAPI app code from either project.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

# Values the gateway accepts in `ReadyPost.post_type` (see
# `maxapi/api/models/jobs.py::ReadyPostType`).
_GATEWAY_POST_TYPES: frozenset[str] = frozenset({"single", "collection", "custom"})

# Values the gateway accepts in `ReadyPostMedia.type`
# (`maxapi/api/models/jobs.py::ReadyPostMediaType`). Note: parser only
# emits "photo" today.
_GATEWAY_MEDIA_TYPES: frozenset[str] = frozenset({"photo", "image", "video", "document"})

# Values the gateway accepts in `ReadyPost.parse_mode`.
_GATEWAY_PARSE_MODES: frozenset[str] = frozenset({"markdown", "html"})


def _coerce_post_type(value: Any) -> str:
    """Map parser-side post types onto the gateway enum.

    The parser may legitimately produce ``visual_poll``, ``discount`` or
    ``scenario`` (per ``wb_parser_development_prompt.txt``). The gateway
    folds them into ``custom`` and keeps the original value in
    ``ReadyPost.metadata.post_type`` once the publication request is built.
    """

    if not isinstance(value, str):
        return "custom"
    return value if value in _GATEWAY_POST_TYPES else "custom"


def _coerce_media_type(value: Any) -> str:
    if not isinstance(value, str):
        return "photo"
    return value if value in _GATEWAY_MEDIA_TYPES else "photo"


def _coerce_parse_mode(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    return value if value in _GATEWAY_PARSE_MODES else None


def translate_ready_post(ready_post: dict[str, Any]) -> dict[str, Any]:
    """Return a gateway-compatible ``ReadyPost`` dict.

    The original payload is **not** mutated. Only fields that the
    gateway is stricter about are rewritten; everything else passes
    through unchanged so future schema additions on the parser side do
    not require touching this function.
    """

    out = deepcopy(ready_post)

    # post_id is `str | int` on the parser side, `str` on the gateway side.
    post_id = out.get("post_id")
    if post_id is not None and not isinstance(post_id, str):
        out["post_id"] = str(post_id)

    out["post_type"] = _coerce_post_type(out.get("post_type"))
    out["parse_mode"] = _coerce_parse_mode(out.get("parse_mode"))

    media = out.get("media") or []
    for item in media:
        if isinstance(item, dict):
            item["type"] = _coerce_media_type(item.get("type"))

    # The gateway's ReactionsHint allows `text=null`, the parser always
    # sets a default. No transform needed.

    return out


def build_publication_job_request(
    ready_post: dict[str, Any],
    *,
    channel_id: str,
    mode: str = "auto",
    publish_at: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the body for ``POST /v1/accounts/{account_id}/publication-jobs``.

    Adressing (which MAX channel to publish to) lives in the bridge's
    config, not in ``ReadyPost`` — keeping the post fully transport-
    agnostic, as required by ``docs/integration_plan.md`` § 5.
    """

    body: dict[str, Any] = {
        "channel_id": channel_id,
        "ready_post": translate_ready_post(ready_post),
        "mode": mode,
    }
    if publish_at is not None:
        body["publish_at"] = publish_at
    if options is not None:
        body["options"] = options
    return body
