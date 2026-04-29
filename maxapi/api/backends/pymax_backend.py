"""Real MAX userbot backend powered by ``maxapi-python``.

This backend opens a long-lived WebSocket session per account and delegates
publish/edit/delete/pin operations to that session. It is intentionally a thin
adapter; complex behaviour (idempotency, rate limiting, scheduling) lives in
the gateway above.

Notes & caveats:

* MAX has no official userbot API. The library reverse-engineers the internal
  WebSocket protocol and may break without warning.
* PyMax stores per-account session state in ``work_dir/<account_id>``; preserve
  this directory across restarts to avoid forcing the user to re-enter SMS
  codes.
* Channel ids in MAX are 64-bit integers. We expose them as decimal strings
  through the API for compatibility with the OpenAPI contract.

Required:

    pip install -e ".[pymax]"
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json as _json
import mimetypes
import os
import re
import shutil
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from api.backends.protocol import (
    LoginChallengeData,
    MaxBackend,
    UpstreamChannel,
    UpstreamMedia,
    UpstreamMessage,
)
from api.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from api.ids import new_id
from api.models.channels import ChannelPermissions, ChannelRole, ChannelType
from api.models.media import MediaType
from api.models.posts import PublishOptions, PublishPostRequest, TextFormat


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _import_pymax() -> Any:
    try:
        import pymax  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - depends on optional dep
        raise ServiceUnavailableError(
            "PyMax backend selected but the maxapi-python package is not "
            "installed. Run `pip install -e \".[pymax]\"`."
        ) from exc
    return pymax


# Markdown grammar PyMax understands plus link support that the wire protocol
# already accepts (verified empirically against api.oneme.ru). Order matters:
# longer / unambiguous tokens first so e.g. ``**bold**`` is matched before
# ``*italic*``.
_MARKDOWN_RE = re.compile(
    r"\[(?P<link_label>[^\]\n]+?)\]\((?P<link_url>(?:https?|tg|max|mailto)[^\s)]+)\)"
    r"|\*\*(?P<strong>.+?)\*\*"
    r"|__(?P<underline>.+?)__"
    r"|~~(?P<strike>.+?)~~"
    r"|\*(?P<emphasized>.+?)\*",
    re.DOTALL,
)


def _utf16_len(s: str) -> int:
    """Length of ``s`` measured in UTF-16 code units.

    The MAX wire protocol encodes ``from`` / ``length`` on message elements
    in UTF-16 code units (same convention as Telegram Bot API entities), not
    Python code points. Characters outside the BMP (most emoji, e.g. 🚀, 🔗,
    🧭) are a single Python code point but two UTF-16 code units, so using
    ``len(s)`` directly shifts every subsequent element to the left and
    truncates the last characters of each span. This helper returns the
    correct UTF-16 length for offset arithmetic.
    """
    return len(s.encode("utf-16-le")) // 2


def _format_markdown(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse a small subset of Markdown into MAX wire ``elements``.

    Supports ``[label](url)`` (rendered as a clickable word-link via
    ``{type: LINK, attributes: {url}}``) plus ``**bold**`` / ``__underline__`` /
    ``~~strike~~`` / ``*italic*``. Returns the cleaned text (markup stripped)
    and the list of element dicts ready to be embedded in a ``MSG_SEND`` /
    ``MSG_EDIT`` payload.

    Offsets and lengths are expressed in UTF-16 code units — see
    :func:`_utf16_len`.
    """
    elements: list[dict[str, Any]] = []
    out: list[str] = []
    pos = 0
    out_pos = 0  # running offset in UTF-16 code units
    for match in _MARKDOWN_RE.finditer(text):
        start, end = match.span()
        if start > pos:
            chunk = text[pos:start]
            out.append(chunk)
            out_pos += _utf16_len(chunk)
        if match.group("link_label") is not None:
            label = match.group("link_label")
            url = match.group("link_url")
            element = {
                "type": "LINK",
                "from": out_pos,
                "length": _utf16_len(label),
                "attributes": {"url": url},
            }
            inner = label
        elif match.group("strong") is not None:
            inner = match.group("strong")
            element = {"type": "STRONG", "from": out_pos, "length": _utf16_len(inner)}
        elif match.group("underline") is not None:
            inner = match.group("underline")
            element = {"type": "UNDERLINE", "from": out_pos, "length": _utf16_len(inner)}
        elif match.group("strike") is not None:
            inner = match.group("strike")
            element = {
                "type": "STRIKETHROUGH",
                "from": out_pos,
                "length": _utf16_len(inner),
            }
        else:
            inner = match.group("emphasized") or ""
            element = {"type": "EMPHASIZED", "from": out_pos, "length": _utf16_len(inner)}
        out.append(inner)
        elements.append(element)
        out_pos += _utf16_len(inner)
        pos = end
    if pos < len(text):
        out.append(text[pos:])
    return "".join(out), elements


def _format_text_for_max(
    text: str, fmt: TextFormat | str | None
) -> tuple[str, list[dict[str, Any]]]:
    """Decide whether to run the markdown parser based on the request's
    ``format`` field. ``plain`` is sent verbatim — the MAX client still
    auto-links bare URLs in the body. ``html`` is currently treated as plain
    (the wire protocol doesn't expose HTML rendering)."""
    fmt_str = str(getattr(fmt, "value", fmt) or "").lower()
    if fmt_str == "markdown":
        return _format_markdown(text)
    return text, []


class _Session:
    """Per-account live PyMax client + bookkeeping."""

    def __init__(self, client: Any, *, work_dir: Path) -> None:
        self.client = client
        self.work_dir = work_dir
        self.background_task: asyncio.Task[None] | None = None
        self.lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self.background_task is not None and not self.background_task.done():
            self.background_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.background_task
        with contextlib.suppress(Exception):
            await self.client.close()


class PyMaxBackend(MaxBackend):
    def __init__(
        self,
        *,
        work_dir: str,
        device_type: str = "DESKTOP",
        app_version: str = "25.12.13",
    ) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.device_type = device_type
        self.app_version = app_version
        self._challenges: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()

    # --------------------------------------------------------------- helpers
    def _user_agent(self) -> Any:
        pymax = _import_pymax()
        return pymax.payloads.UserAgentPayload(
            device_type=self.device_type, app_version=self.app_version
        )

    def _build_client(self, *, work_dir: Path, phone: str) -> Any:
        pymax = _import_pymax()
        work_dir.mkdir(parents=True, exist_ok=True)
        # MaxClient (WebSocketMixin) only allows device_type=WEB (QR-code login
        # against the web client). Phone+SMS authentication requires the
        # SocketMaxClient flavour, which speaks the native TCP/SOCKET protocol
        # used by the mobile/desktop apps and accepts ANDROID/IOS/DESKTOP UA's.
        client_cls = (
            pymax.MaxClient
            if str(self.device_type).upper() == "WEB"
            else pymax.SocketMaxClient
        )
        return client_cls(
            phone=phone,
            work_dir=str(work_dir),
            headers=self._user_agent(),
            reconnect=True,
        )

    async def _session(self, account_id: str) -> _Session:
        async with self._lock:
            session = self._sessions.get(account_id)
        if session is None:
            raise UnauthorizedError(
                "Account session is not active. Re-authenticate via /v1/accounts/login.",
                code="session_inactive",
            )
        return session

    async def _attach_session(
        self, account_id: str, *, work_dir: Path, phone: str
    ) -> _Session:
        """Build a fresh client bound to ``work_dir`` (which already contains
        the session.db with the auth token), run ``client.start()`` in the
        background, and wait until the session is ONLINE (``client.me`` set).
        """
        client = self._build_client(work_dir=work_dir, phone=phone)
        session = _Session(client=client, work_dir=work_dir)

        async def _run_start() -> None:
            try:
                await client.start()
            except Exception:  # noqa: BLE001 - keep gateway alive
                pass

        session.background_task = asyncio.create_task(_run_start())

        # Wait for sync to populate client.me; that means LOGIN op succeeded
        # and the session is in ONLINE state.
        deadline = asyncio.get_event_loop().time() + 30.0
        while asyncio.get_event_loop().time() < deadline:
            if getattr(client, "me", None) is not None:
                break
            if session.background_task.done():
                # start() exited early — propagate the failure.
                break
            await asyncio.sleep(0.2)
        if getattr(client, "me", None) is None:
            await session.aclose()
            raise ServiceUnavailableError(
                "MAX upstream session did not reach ONLINE state within 30s.",
                code="upstream_not_online",
            )

        async with self._lock:
            previous = self._sessions.pop(account_id, None)
            self._sessions[account_id] = session
        if previous is not None:
            await previous.aclose()
        return session

    @staticmethod
    def _coerce_chat_id(channel_id: str) -> int:
        try:
            return int(channel_id)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(
                f"Channel id {channel_id!r} is not a valid MAX chat id."
            ) from exc

    @staticmethod
    def _channel_from_pymax(chat: Any) -> UpstreamChannel:
        title = getattr(chat, "title", None) or getattr(chat, "name", None) or "Channel"
        username = getattr(chat, "username", None) or getattr(chat, "link", None)
        public_link = getattr(chat, "link", None) or getattr(chat, "invite_link", None)
        members_count = getattr(chat, "members_count", None) or getattr(chat, "participants_count", None)
        type_value = str(getattr(chat, "type", "") or "").lower()
        chan_type = ChannelType.channel if "channel" in type_value else ChannelType.chat
        return UpstreamChannel(
            channel_id=str(getattr(chat, "id", "")),
            title=str(title),
            username=username,
            public_link=public_link,
            type=chan_type,
            role=ChannelRole.owner,
            members_count=members_count,
            permissions=ChannelPermissions(
                can_publish=True,
                can_edit=True,
                can_delete=True,
                can_pin=True,
                can_upload_media=True,
            ),
        )

    # ----------------------------------------------------------------- login
    async def start_login(
        self,
        *,
        phone: str,
        device_name: str,
        callback_url: str | None,
    ) -> LoginChallengeData:
        challenge_id = new_id("chg")
        work_dir = self.work_dir / "_pending" / challenge_id
        client = self._build_client(work_dir=work_dir, phone=phone)
        await client.connect(self._user_agent())
        try:
            temp_token = await client.request_code(phone=phone)
        except Exception as exc:  # noqa: BLE001 - convert to API error
            with contextlib.suppress(Exception):
                await client.close()
            raise BadRequestError(
                f"MAX upstream rejected request_code: {exc}",
                code="upstream_reject",
            ) from exc
        expires = _utcnow() + timedelta(minutes=10)
        async with self._lock:
            self._challenges[challenge_id] = {
                "phone": phone,
                "device_name": device_name,
                "callback_url": callback_url,
                "temp_token": temp_token,
                "client": client,
                "work_dir": work_dir,
                "expires_at": expires,
            }
        return LoginChallengeData(
            challenge_id=challenge_id,
            expires_at=expires,
            delivery="sms",
            masked_destination=phone[:-4].replace(phone[-8:-4], "****") + phone[-4:],
            backend_state={"challenge_id": challenge_id},
        )

    async def verify_login(
        self,
        *,
        challenge_state: dict[str, Any],
        code: str,
        two_factor_password: str | None,
    ) -> dict[str, Any]:
        challenge_id = challenge_state.get("challenge_id")
        async with self._lock:
            entry = self._challenges.pop(challenge_id, None) if challenge_id else None
        if entry is None:
            raise NotFoundError("Login challenge not found.", code="challenge_not_found")
        if _utcnow() > entry["expires_at"]:
            with contextlib.suppress(Exception):
                await entry["client"].close()
            raise ConflictError("Login challenge has expired.", code="challenge_expired")

        client = entry["client"]
        try:
            # start=False just sends the code, persists the auth token to
            # session.db, and returns. We close the temp client immediately
            # afterwards because the SMS-challenge socket is single-purpose;
            # attach_account() opens a fresh long-lived client.
            await client.login_with_code(entry["temp_token"], code, start=False)
        except Exception as exc:  # noqa: BLE001
            with contextlib.suppress(Exception):
                await client.close()
            raise ConflictError(
                f"Verification rejected by MAX upstream: {exc}",
                code="invalid_code",
            ) from exc
        with contextlib.suppress(Exception):
            await client.close()

        return {
            "phone": entry["phone"],
            "device_name": entry["device_name"],
            "max_user_id": None,
            "username": None,
            "display_name": entry["device_name"] or "MAX user",
            # Returned for the gateway to attach the live client to the new
            # account_id once it has been minted.
            "_pymax_pending": {
                "phone": entry["phone"],
                "work_dir": str(entry["work_dir"]),
            },
        }

    async def attach_account(
        self, *, account_id: str, pending: dict[str, Any]
    ) -> None:
        """Bind a live PyMax session to ``account_id``.

        After ``verify_login`` saves the auth token to ``session.db`` the temp
        client used for the SMS challenge is closed; this method moves the
        session DB to a stable per-account directory and spins up a fresh
        long-lived client that loads it and reaches ONLINE state.
        """
        old_dir = Path(pending["work_dir"])
        new_dir = self.work_dir / account_id
        if old_dir.exists() and not new_dir.exists():
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(old_dir, new_dir)
            except OSError:
                shutil.move(str(old_dir), str(new_dir))
        phone = pending.get("phone", "")
        # Persist a minimal manifest so we can resume the session on the next
        # gateway restart without forcing the user through SMS again.
        try:
            (new_dir / "account.json").write_text(
                _json.dumps(
                    {
                        "account_id": account_id,
                        "phone": phone,
                        "display_name": pending.get("display_name") or "MAX user",
                    }
                ),
                encoding="utf-8",
            )
        except OSError:  # pragma: no cover - best effort
            pass
        await self._attach_session(account_id, work_dir=new_dir, phone=phone)

    async def resume_existing_accounts(self) -> list[dict[str, Any]]:
        """Re-attach every ``acc_*/session.db`` cached on disk.

        Called by the FastAPI lifespan on startup so live sessions survive
        process restarts. Failures for individual directories are swallowed —
        the user can always re-authenticate via the API.
        """
        resumed: list[dict[str, Any]] = []
        if not self.work_dir.exists():
            return resumed
        for entry in sorted(self.work_dir.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("acc_"):
                continue
            if not (entry / "session.db").exists():
                continue
            manifest_path = entry / "account.json"
            phone = ""
            display_name = "MAX user"
            if manifest_path.exists():
                try:
                    raw = _json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                    phone = raw.get("phone") or ""
                    display_name = raw.get("display_name") or display_name
                except (OSError, ValueError):
                    pass
            if not phone:
                # Without a phone PyMax can't construct a client; skip and
                # let the user re-login.
                continue
            try:
                session = await self._attach_session(
                    entry.name, work_dir=entry, phone=phone
                )
            except Exception:  # noqa: BLE001 - skip broken caches
                continue
            me = getattr(session.client, "me", None)
            max_user_id = str(getattr(me, "id", "")) if me is not None else None
            real_display = display_name
            names = getattr(me, "names", None) or []
            if names:
                first = getattr(names[0], "first_name", None)
                if first:
                    real_display = first
            resumed.append(
                {
                    "account_id": entry.name,
                    "phone": phone,
                    "display_name": real_display,
                    "max_user_id": max_user_id,
                    "username": getattr(me, "username", None) if me else None,
                }
            )
        return resumed

    async def logout(self, *, account_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(account_id, None)
        if session is None:
            return
        with contextlib.suppress(Exception):
            await session.client.logout()
        await session.aclose()

    # -------------------------------------------------------------- channels
    async def list_channels(self, *, account_id: str) -> list[UpstreamChannel]:
        session = await self._session(account_id)
        try:
            chats = await session.client.fetch_chats()
        except Exception as exc:  # noqa: BLE001
            raise ServiceUnavailableError(
                f"MAX upstream did not return chats: {exc}"
            ) from exc
        return [self._channel_from_pymax(chat) for chat in chats]

    async def resolve_channel(
        self, *, account_id: str, link: str
    ) -> UpstreamChannel | None:
        session = await self._session(account_id)
        normalized = link.lstrip("@").strip()
        try:
            chat = await session.client.resolve_channel_by_name(normalized)
        except Exception:  # noqa: BLE001
            chat = None
        if chat is None:
            return None
        return self._channel_from_pymax(chat)

    # ----------------------------------------------------------------- media
    async def upload_media(
        self,
        *,
        account_id: str,
        media_type: MediaType,
        content: bytes,
        filename: str | None,
        mime_type: str | None,
        caption: str | None,
    ) -> UpstreamMedia:
        # PyMax uploads media as part of send_message via Photo/File/Video
        # objects, so we just persist the bytes locally and attach them at
        # publish time via the gateway's ``materialize_media_from_refs``.
        await self._session(account_id)  # ensure session exists
        session_dir = self.work_dir / account_id / "media"
        session_dir.mkdir(parents=True, exist_ok=True)
        media_id = new_id("med")
        target = session_dir / f"{media_id}-{filename or 'blob'}"
        target.write_bytes(content)
        return UpstreamMedia(
            media_id=media_id,
            type=media_type,
            status="uploaded",
            url=str(target),
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            attachment={"local_path": str(target), "caption": caption} if caption else {"local_path": str(target)},
            created_at=_utcnow(),
        )

    async def import_media(
        self,
        *,
        account_id: str,
        media_type: MediaType,
        url: str,
        filename: str | None,
        caption: str | None,
        source_post_id: str | None,
    ) -> UpstreamMedia:
        await self._session(account_id)
        return UpstreamMedia(
            media_id=new_id("med"),
            type=media_type,
            status="ready",
            url=url,
            filename=filename,
            mime_type=None,
            size_bytes=None,
            attachment={"remote_url": url, "source_post_id": source_post_id},
            created_at=_utcnow(),
        )

    # ----------------------------------------------------------------- posts
    async def _build_attachments(
        self, session: _Session, media: list[UpstreamMedia]
    ) -> list[Any]:
        if not media:
            return []
        pymax = _import_pymax()
        attachments: list[Any] = []
        for item in media:
            attachment_meta = item.attachment or {}
            local_path = attachment_meta.get("local_path") if attachment_meta else None
            remote_url = (
                attachment_meta.get("remote_url") if attachment_meta else None
            ) or item.url
            # Materialize remote URLs first so ReadyPost media refs without
            # backend attachment metadata still produce real MAX attachments.
            kwargs: dict[str, Any] = {}
            if local_path:
                kwargs["path"] = local_path
            elif remote_url:
                kwargs["path"] = await self._download_remote_media(
                    session, item, remote_url
                )
            else:
                continue
            try:
                if item.type is MediaType.image:
                    attachments.append(pymax.Photo(**kwargs))
                elif item.type is MediaType.video:
                    video_cls = getattr(pymax, "Video", pymax.File)
                    attachments.append(video_cls(**kwargs))
                else:
                    attachments.append(pymax.File(**kwargs))
            except Exception as exc:  # noqa: BLE001
                raise ServiceUnavailableError(
                    f"MAX attachment preparation failed: {exc}",
                    code="attachment_prepare_failed",
                ) from exc
        return attachments

    async def _download_remote_media(
        self,
        session: _Session,
        item: UpstreamMedia,
        url: str,
    ) -> str:
        cache_dir = session.work_dir / "media_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(urlparse(url).path).suffix.lower()
        if not suffix:
            suffix = mimetypes.guess_extension(item.mime_type or "") or ".jpg"
        target = cache_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]}{suffix}"
        if target.exists() and target.stat().st_size > 0:
            return str(target)
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=30.0,
                trust_env=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
            target.write_bytes(response.content)
        except Exception as exc:  # noqa: BLE001
            with contextlib.suppress(OSError):
                target.unlink()
            raise ServiceUnavailableError(
                f"Failed to materialize remote media: {exc}",
                code="media_download_failed",
            ) from exc
        return str(target)

    async def _upload_attachments(
        self, session: _Session, items: list[Any]
    ) -> list[dict[str, Any]]:
        """Run PyMax's per-attachment upload (each returns a dict ready to
        embed into ``message.attaches`` on the wire)."""
        attaches: list[dict[str, Any]] = []
        for item in items:
            uploaded = await session.client._upload_attachment(item)
            if not uploaded:
                raise ServiceUnavailableError(
                    "MAX upstream refused to upload an attachment.",
                    code="upstream_upload_failed",
                )
            attaches.append(uploaded)
        return attaches

    async def _msg_send_or_edit(
        self,
        session: _Session,
        *,
        opcode_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Speak the raw MAX wire protocol so we can embed rich elements
        (e.g. ``LINK`` with ``attributes.url``) that PyMax's high-level
        ``send_message`` / ``edit_message`` cannot serialize."""
        pymax = _import_pymax()
        opcode = getattr(pymax.Opcode, opcode_name)
        data = await session.client._send_and_wait(opcode=opcode, payload=payload)
        body = data.get("payload") if isinstance(data, dict) else None
        if not isinstance(body, dict):
            raise ServiceUnavailableError("MAX upstream returned no payload.")
        if body.get("error"):
            raise ServiceUnavailableError(
                f"MAX upstream rejected {opcode_name}: {body.get('error')!r} "
                f"({body.get('localizedMessage') or body.get('message') or ''})",
                code="upstream_error",
            )
        return body

    async def publish_message(
        self,
        *,
        account_id: str,
        channel_id: str,
        request: PublishPostRequest,
        options: PublishOptions | None,
        media: list[UpstreamMedia],
    ) -> UpstreamMessage:
        session = await self._session(account_id)
        chat_id = self._coerce_chat_id(channel_id)
        wrapped = await self._build_attachments(session, media)
        notify = bool(options is None or options.disable_notification is False)
        clean_text, elements = _format_text_for_max(
            request.text or "", request.format
        )
        async with session.lock:
            attaches = await self._upload_attachments(session, wrapped)
            payload = {
                "chatId": chat_id,
                "message": {
                    "text": clean_text,
                    "cid": int(_time.time() * 1000),
                    "elements": elements,
                    "attaches": attaches,
                    "link": None,
                },
                "notify": notify,
            }
            try:
                body = await self._msg_send_or_edit(
                    session, opcode_name="MSG_SEND", payload=payload
                )
            except ServiceUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ServiceUnavailableError(
                    f"MAX upstream rejected send_message: {exc}"
                ) from exc
        message = body.get("message") or {}
        return UpstreamMessage(
            message_id=str(message.get("id") or ""),
            channel_id=str(body.get("chatId") or channel_id),
            text=message.get("text", clean_text),
            permalink=None,
            raw=None,
        )

    async def edit_message(
        self,
        *,
        account_id: str,
        channel_id: str,
        message_id: str,
        text: str | None,
        media: list[UpstreamMedia] | None,
    ) -> UpstreamMessage:
        session = await self._session(account_id)
        chat_id = self._coerce_chat_id(channel_id)
        wrapped = await self._build_attachments(session, media or [])
        # Edits don't currently surface a ``format`` flag through the
        # protocol; reuse the markdown parser whenever the new text contains
        # an obvious ``[label](url)`` so word-links keep working when the
        # post is updated.
        fmt: TextFormat | str = (
            TextFormat.markdown if text and "](" in text else TextFormat.plain
        )
        clean_text, elements = _format_text_for_max(text or "", fmt)
        async with session.lock:
            attaches = await self._upload_attachments(session, wrapped)
            payload = {
                "chatId": chat_id,
                "messageId": int(message_id),
                "text": clean_text,
                "elements": elements,
                "attaches": attaches,
            }
            try:
                body = await self._msg_send_or_edit(
                    session, opcode_name="MSG_EDIT", payload=payload
                )
            except ServiceUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ServiceUnavailableError(
                    f"MAX upstream rejected edit_message: {exc}"
                ) from exc
        message = body.get("message") or {}
        return UpstreamMessage(
            message_id=str(message.get("id") or message_id),
            channel_id=channel_id,
            text=message.get("text", clean_text),
            permalink=None,
            raw=None,
        )

    async def delete_message(
        self, *, account_id: str, channel_id: str, message_id: str
    ) -> None:
        session = await self._session(account_id)
        chat_id = self._coerce_chat_id(channel_id)
        async with session.lock:
            try:
                await session.client.delete_message(
                    chat_id=chat_id,
                    message_ids=[int(message_id)],
                    for_me=False,
                )
            except Exception as exc:  # noqa: BLE001
                raise ServiceUnavailableError(
                    f"MAX upstream rejected delete_message: {exc}"
                ) from exc

    async def pin_message(
        self,
        *,
        account_id: str,
        channel_id: str,
        message_id: str,
        notify: bool,
    ) -> None:
        session = await self._session(account_id)
        chat_id = self._coerce_chat_id(channel_id)
        async with session.lock:
            try:
                await session.client.pin_message(
                    chat_id=chat_id,
                    message_id=int(message_id),
                    notify_pin=notify,
                )
            except Exception as exc:  # noqa: BLE001
                raise ServiceUnavailableError(
                    f"MAX upstream rejected pin_message: {exc}"
                ) from exc

    async def unpin_message(
        self, *, account_id: str, channel_id: str, message_id: str
    ) -> None:
        # PyMax 1.2.x has no dedicated unpin call; pinning a noop message-id of
        # 0 effectively clears the pin in the official client. Fail loudly so
        # the gateway can fall back to a manual flow.
        raise ServiceUnavailableError(
            "MAX upstream does not yet expose an unpin operation through the "
            "PyMax client."
        )

    # ---------------------------------------------------------------- close
    async def aclose(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.aclose()
