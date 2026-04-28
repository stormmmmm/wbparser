from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge.translator import build_publication_job_request, translate_ready_post

_SAMPLE_PATH = Path(__file__).resolve().parents[2] / "data" / "outbox" / "ready_posts_sample.json"


def _sample() -> dict:
    return json.loads(_SAMPLE_PATH.read_text(encoding="utf-8"))


def test_translate_keeps_canonical_payload_intact():
    payload = _sample()
    out = translate_ready_post(payload)

    # Untouched fields go through verbatim.
    assert out["post_id"] == payload["post_id"]
    assert out["title"] == payload["title"]
    assert out["text"] == payload["text"]
    assert out["fresh_until"] == payload["fresh_until"]
    assert out["items"] == payload["items"]
    assert out["buttons"] == payload["buttons"]
    assert out["source"] == "wb_parser"

    # post_type "collection" is in the gateway enum, so it stays.
    assert out["post_type"] == "collection"

    # media[].type "photo" is in the gateway enum, so it stays.
    assert out["media"][0]["type"] == "photo"

    # The original payload must NOT be mutated.
    assert payload == _sample()


@pytest.mark.parametrize(
    ("incoming", "expected"),
    [
        ("single", "single"),
        ("collection", "collection"),
        ("custom", "custom"),
        ("visual_poll", "custom"),
        ("discount", "custom"),
        ("scenario", "custom"),
        ("", "custom"),
        (None, "custom"),
    ],
)
def test_post_type_is_folded_into_gateway_enum(incoming, expected):
    payload = _sample()
    payload["post_type"] = incoming
    out = translate_ready_post(payload)
    assert out["post_type"] == expected


def test_post_id_is_coerced_to_string():
    payload = _sample()
    payload["post_id"] = 12345
    out = translate_ready_post(payload)
    assert out["post_id"] == "12345"
    assert isinstance(out["post_id"], str)


def test_unknown_media_type_falls_back_to_photo():
    payload = _sample()
    payload["media"][0]["type"] = "carousel"
    out = translate_ready_post(payload)
    assert out["media"][0]["type"] == "photo"


def test_unknown_parse_mode_is_dropped():
    payload = _sample()
    payload["parse_mode"] = "telegram-html"
    out = translate_ready_post(payload)
    assert out["parse_mode"] is None


def test_build_publication_job_request_carries_addressing_outside_ready_post():
    payload = _sample()
    body = build_publication_job_request(payload, channel_id="ch-test")

    assert body["channel_id"] == "ch-test"
    assert body["mode"] == "auto"
    assert "publish_at" not in body
    assert "options" not in body

    # Addressing must NOT be smuggled into ReadyPost itself.
    assert "target_account_id" not in body["ready_post"]
    assert "target_channel_id" not in body["ready_post"]
