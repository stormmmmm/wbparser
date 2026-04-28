"""Tests for the MAX markdown parser in api.backends.pymax_backend.

Crucially covers the UTF-16 offset math — MAX expects ``from``/``length`` on
message elements in UTF-16 code units, matching the Telegram convention. Any
off-by-N bug here visually "shifts" entities left/right in the client.
"""

from __future__ import annotations

from api.backends.pymax_backend import _format_markdown, _utf16_len


def test_utf16_len_bmp_chars() -> None:
    assert _utf16_len("") == 0
    assert _utf16_len("abc") == 3
    assert _utf16_len("Открыть Google") == len("Открыть Google")  # all BMP


def test_utf16_len_non_bmp_emoji_counts_as_two_units() -> None:
    # Each non-BMP emoji is 1 Python code point but 2 UTF-16 code units.
    assert _utf16_len("🧭") == 2
    assert _utf16_len("🔗") == 2
    assert _utf16_len("✨") == 1  # BMP sparkle is a single UTF-16 unit
    assert _utf16_len("🧭🔗") == 4


def test_markdown_link_plain_ascii() -> None:
    cleaned, elements = _format_markdown("Click [here](https://example.com) now")
    assert cleaned == "Click here now"
    assert elements == [
        {
            "type": "LINK",
            "from": 6,
            "length": 4,
            "attributes": {"url": "https://example.com"},
        }
    ]


def test_markdown_link_offsets_account_for_non_bmp_emoji() -> None:
    # Text mirrors the real post the user flagged: two non-BMP emojis before
    # the word-link. With Python-char offsets the LINK would start at 14 and
    # miss the last two characters of "Открыть Google"; in UTF-16 units the
    # span must be 16..30 so the full label is covered.
    text = "🧭 🔗 [Открыть Google](https://google.com)"
    cleaned, elements = _format_markdown(text)
    assert cleaned == "🧭 🔗 Открыть Google"
    assert len(elements) == 1
    link = elements[0]
    assert link["type"] == "LINK"
    assert link["attributes"] == {"url": "https://google.com"}

    utf16 = cleaned.encode("utf-16-le")
    start = link["from"]
    length = link["length"]
    slice_utf16 = utf16[start * 2 : (start + length) * 2]
    assert slice_utf16.decode("utf-16-le") == "Открыть Google"


def test_markdown_mixed_formatting_with_emoji_and_word_link() -> None:
    text = (
        "🚀 [Открыть Google](https://google.com), "
        "а ещё **жирный** и __подчёрк__"
    )
    cleaned, elements = _format_markdown(text)
    assert cleaned == "🚀 Открыть Google, а ещё жирный и подчёрк"

    utf16 = cleaned.encode("utf-16-le")

    def slice_by(element: dict[str, object]) -> str:
        start = int(element["from"])
        length = int(element["length"])
        return utf16[start * 2 : (start + length) * 2].decode("utf-16-le")

    by_type = {e["type"]: e for e in elements}
    assert slice_by(by_type["LINK"]) == "Открыть Google"
    assert by_type["LINK"]["attributes"] == {"url": "https://google.com"}
    assert slice_by(by_type["STRONG"]) == "жирный"
    assert slice_by(by_type["UNDERLINE"]) == "подчёрк"


def test_markdown_plain_has_no_elements() -> None:
    cleaned, elements = _format_markdown("plain text with 🚀 emoji")
    assert cleaned == "plain text with 🚀 emoji"
    assert elements == []
