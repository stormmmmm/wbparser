"""Tests for the high-level Python SDK (`api.MaxUserBot`)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api import MaxUserBot
from api.backends import InMemoryBackend
from api.errors import ConflictError, NotFoundError
from api.models.posts import PublishedPostStatus, TextFormat
from api.storage import Storage


@pytest.fixture()
async def bot() -> MaxUserBot:
    instance = MaxUserBot(backend=InMemoryBackend(), storage=Storage())
    await instance.start()
    return instance


async def _login(bot: MaxUserBot) -> str:
    challenge = await bot.start_login("+79991234567")
    account = await bot.verify_login(challenge.challenge_id, code="000000")
    return account.account_id


async def test_health_does_not_require_login(bot: MaxUserBot) -> None:
    health = bot.health()
    assert health.status.value == "ok"


async def test_login_creates_account(bot: MaxUserBot) -> None:
    challenge = await bot.start_login("+79991234567")
    assert challenge.challenge_id.startswith("chg_")
    assert challenge.masked_destination is not None
    assert challenge.delivery.value == "sms"

    account = await bot.verify_login(challenge.challenge_id, code="000000")
    assert account.account_id.startswith("acc_")
    assert account.status.value == "connected"

    fetched = bot.get_account(account.account_id)
    assert fetched.account_id == account.account_id

    status = bot.get_account_status(account.account_id)
    assert status.can_publish is True


async def test_invalid_sms_code_is_rejected(bot: MaxUserBot) -> None:
    challenge = await bot.start_login("+79991234567")
    with pytest.raises(ConflictError):
        await bot.verify_login(challenge.challenge_id, code="111111")


async def test_list_channels_and_resolve(bot: MaxUserBot) -> None:
    account_id = await _login(bot)
    channels = await bot.list_channels(account_id)
    assert any(c.title == "WB Finds (demo)" for c in channels)
    resolved = await bot.resolve_channel(account_id, "wb_finds_demo")
    assert resolved.channel_id == channels[0].channel_id


async def test_find_channel_by_exact_title(bot: MaxUserBot) -> None:
    account_id = await _login(bot)
    channel = await bot.find_channel(account_id, title="WB Finds (demo)")
    assert channel.title == "WB Finds (demo)"


async def test_find_channel_case_insensitive_by_default(bot: MaxUserBot) -> None:
    account_id = await _login(bot)
    channel = await bot.find_channel(account_id, title="wb finds (DEMO)")
    assert channel.title == "WB Finds (demo)"


async def test_find_channel_substring_with_exact_false(bot: MaxUserBot) -> None:
    account_id = await _login(bot)
    channel = await bot.find_channel(account_id, title="Finds", exact=False)
    assert channel.title == "WB Finds (demo)"


async def test_find_channel_not_found_raises(bot: MaxUserBot) -> None:
    account_id = await _login(bot)
    with pytest.raises(NotFoundError):
        await bot.find_channel(account_id, title="nonexistent channel")


async def test_find_channels_returns_list(bot: MaxUserBot) -> None:
    account_id = await _login(bot)
    matches = await bot.find_channels(
        account_id, title="demo", exact=False
    )
    assert matches and all("demo" in c.title.lower() for c in matches)


async def test_upload_and_publish_with_markdown_word_link(bot: MaxUserBot) -> None:
    account_id = await _login(bot)
    channels = await bot.list_channels(account_id)
    channel_id = channels[0].channel_id

    photo = await bot.upload_media(
        account_id,
        type="image",
        file=b"\xff\xd8\xff\xd9",  # smallest valid JPEG marker pair
        filename="probe.jpg",
        mime_type="image/jpeg",
    )
    assert photo.media_id.startswith("med_")

    post = await bot.publish_post(
        account_id,
        channel_id,
        text="🚀 [Открыть Google](https://google.com), а ещё **жирный**.",
        format="markdown",
        media=[photo, photo.media_id],  # mix object + media_id string
        disable_notification=True,
    )
    assert post.status == PublishedPostStatus.published
    assert post.format == TextFormat.markdown
    assert len(post.media) == 2
    assert post.message_id

    # round-trip via storage
    again = bot.get_post(account_id, channel_id, post.message_id)
    assert again.message_id == post.message_id


async def test_publish_text_too_long_raises(bot: MaxUserBot) -> None:
    account_id = await _login(bot)
    channels = await bot.list_channels(account_id)
    with pytest.raises(ValidationError):
        await bot.publish_post(
            account_id,
            channels[0].channel_id,
            text="x" * 4500,
        )


async def test_idempotency_returns_same_post(bot: MaxUserBot) -> None:
    account_id = await _login(bot)
    channels = await bot.list_channels(account_id)
    first = await bot.publish_post(
        account_id,
        channels[0].channel_id,
        text="Hello idempotent",
        idempotency_key="abc-123",
    )
    second = await bot.publish_post(
        account_id,
        channels[0].channel_id,
        text="Hello idempotent",
        idempotency_key="abc-123",
    )
    assert first.message_id == second.message_id


async def test_edit_and_delete_post(bot: MaxUserBot) -> None:
    account_id = await _login(bot)
    channels = await bot.list_channels(account_id)
    channel_id = channels[0].channel_id
    post = await bot.publish_post(account_id, channel_id, text="hi")

    edited = await bot.edit_post(
        account_id, channel_id, post.message_id, text="hi (edited)"
    )
    assert edited.text == "hi (edited)"
    assert edited.status == PublishedPostStatus.edited

    await bot.delete_post(account_id, channel_id, post.message_id)
    with pytest.raises(NotFoundError):
        bot.get_post(account_id, channel_id, post.message_id)


async def test_pin_unpin_post(bot: MaxUserBot) -> None:
    account_id = await _login(bot)
    channels = await bot.list_channels(account_id)
    channel_id = channels[0].channel_id
    post = await bot.publish_post(account_id, channel_id, text="pin me")

    assert await bot.pin_post(account_id, channel_id, post.message_id) is True
    assert bot.get_post(account_id, channel_id, post.message_id).pinned is True
    assert await bot.unpin_post(account_id, channel_id, post.message_id) is True
    assert bot.get_post(account_id, channel_id, post.message_id).pinned is False


async def test_publish_unknown_channel_falls_back_to_resolve(bot: MaxUserBot) -> None:
    account_id = await _login(bot)
    # Drop the channel from local storage to ensure SDK refreshes from backend.
    bot.storage.account_channels[account_id] = set()
    bot.storage.channels.clear()
    post = await bot.publish_post(
        account_id, "-1001111111111", text="published after auto-resolve"
    )
    assert post.message_id


async def test_async_context_manager_starts_and_closes() -> None:
    async with MaxUserBot(backend=InMemoryBackend(), storage=Storage()) as bot:
        before = len(bot.list_accounts())
        challenge = await bot.start_login("+79991234567")
        await bot.verify_login(challenge.challenge_id, code="000000")
        assert len(bot.list_accounts()) == before + 1
