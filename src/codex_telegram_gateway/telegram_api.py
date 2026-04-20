from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


LOGGER = logging.getLogger("codex_telegram_gateway.telegram_api")


@dataclass(frozen=True, slots=True)
class TelegramTransportCapabilities:
    message_reactions: bool = True
    chat_actions: bool = True
    message_edits: bool = True


@dataclass(frozen=True, slots=True)
class TelegramDeliveryResult:
    mode: str
    message: dict


class TelegramApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        method: str,
        error_code: int | None = None,
        description: str = "",
        retry_after: int | None = None,
        classification: str = "unknown",
        fallback_allowed: bool = False,
        final_delivery_risk: bool = True,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.error_code = error_code
        self.description = description
        self.retry_after = retry_after
        self.classification = classification
        self.fallback_allowed = fallback_allowed
        self.final_delivery_risk = final_delivery_risk


def _log_extra(**fields: object) -> dict[str, dict[str, object]]:
    return {"extra_fields": fields}


def _classify_error(method: str, error_code: int | None, description: str, retry_after: int | None) -> tuple[str, bool, bool]:
    normalized = description.lower()
    fallback_allowed = method in {"setMessageReaction", "sendChatAction", "editMessageText"}
    if retry_after is not None or error_code == 429 or "too many requests" in normalized:
        return "rate_limited", fallback_allowed, method == "sendMessage"
    if isinstance(error_code, int) and error_code >= 500:
        return "server_error", fallback_allowed, method == "sendMessage"
    if "timed out" in normalized or "timeout" in normalized:
        return "timeout", fallback_allowed, method == "sendMessage"
    if "not found" in normalized:
        return "not_found", fallback_allowed, method == "sendMessage"
    if "can't be edited" in normalized or "message is not modified" in normalized:
        return "edit_conflict", fallback_allowed, False
    if (
        "reaction" in normalized
        and ("not supported" in normalized or "not enough rights" in normalized or "unsupported" in normalized)
    ) or "method not found" in normalized:
        return "unsupported", fallback_allowed, method == "sendMessage"
    if "chat action" in normalized and ("not supported" in normalized or "unsupported" in normalized):
        return "unsupported", fallback_allowed, False
    if "chat not found" in normalized or "bot was blocked" in normalized or "forbidden" in normalized:
        return "forbidden", False, True
    return "bad_request", fallback_allowed, method == "sendMessage"


def _parse_telegram_error(
    method: str,
    *,
    error_code: int | None,
    description: str,
    retry_after: int | None,
) -> TelegramApiError:
    classification, fallback_allowed, final_delivery_risk = _classify_error(method, error_code, description, retry_after)
    return TelegramApiError(
        f"Telegram API request failed for {method}: {description}",
        method=method,
        error_code=error_code,
        description=description,
        retry_after=retry_after,
        classification=classification,
        fallback_allowed=fallback_allowed,
        final_delivery_risk=final_delivery_risk,
    )


class TelegramApi:
    def __init__(self, token: str, api_base: str) -> None:
        self.base_url = f"{api_base.rstrip('/')}/bot{token}"
        self._capabilities: dict[int, TelegramTransportCapabilities] = {}

    async def call(self, method: str, payload: dict | None = None) -> dict:
        return await asyncio.to_thread(self._call_sync, method, payload or {})

    def _call_sync(self, method: str, payload: dict) -> dict:
        data = urlencode(payload).encode("utf-8")
        request = Request(f"{self.base_url}/{method}", data=data)
        try:
            with urlopen(request, timeout=60) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            parsed = self._decode_error_body(body)
            description = str(parsed.get("description") or exc)
            raise _parse_telegram_error(
                method,
                error_code=parsed.get("error_code", exc.code),
                description=description,
                retry_after=self._extract_retry_after(parsed),
            ) from exc
        except URLError as exc:
            raise TelegramApiError(
                f"Telegram API request failed for {method}: {exc}",
                method=method,
                description=str(exc),
                classification="network",
                fallback_allowed=method in {"setMessageReaction", "sendChatAction", "editMessageText"},
                final_delivery_risk=method == "sendMessage",
            ) from exc
        if not parsed.get("ok"):
            raise _parse_telegram_error(
                method,
                error_code=parsed.get("error_code"),
                description=str(parsed.get("description", parsed)),
                retry_after=self._extract_retry_after(parsed),
            )
        return parsed["result"]

    def _decode_error_body(self, body: str) -> dict:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return {"description": body}
        if isinstance(parsed, dict):
            return parsed
        return {"description": body}

    def _extract_retry_after(self, parsed: dict) -> int | None:
        parameters = parsed.get("parameters")
        if not isinstance(parameters, dict):
            return None
        retry_after = parameters.get("retry_after")
        return retry_after if isinstance(retry_after, int) else None

    def capabilities_for(self, chat_id: int) -> TelegramTransportCapabilities:
        return self._capabilities.get(chat_id, TelegramTransportCapabilities())

    def _update_capabilities(self, chat_id: int, **changes: bool) -> TelegramTransportCapabilities:
        updated = replace(self.capabilities_for(chat_id), **changes)
        self._capabilities[chat_id] = updated
        LOGGER.info("transport_capability_downgraded", extra=_log_extra(chat_id=chat_id, **changes))
        return updated

    async def get_updates(self, offset: int | None, timeout_seconds: int) -> list[dict]:
        payload = {"timeout": timeout_seconds}
        if offset is not None:
            payload["offset"] = offset
        return await self.call("getUpdates", payload)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        thread_id: int | None = None,
        reply_to_message_id: int | None = None,
        reply_markup: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"chat_id": chat_id, "text": text}
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self.call("sendMessage", payload)

    async def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup: str | None = None) -> dict:
        payload: dict[str, object] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self.call("editMessageText", payload)

    async def send_chat_action(self, chat_id: int, action: str, thread_id: int | None = None) -> dict:
        payload: dict[str, object] = {"chat_id": chat_id, "action": action}
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        return await self.call("sendChatAction", payload)

    async def send_typing_action(self, chat_id: int, thread_id: int | None = None) -> bool:
        if not self.capabilities_for(chat_id).chat_actions:
            LOGGER.debug("typing_skipped", extra=_log_extra(chat_id=chat_id, thread_id=thread_id, reason="capability_disabled"))
            return False
        try:
            await self.send_chat_action(chat_id, "typing", thread_id)
        except TelegramApiError as exc:
            if exc.classification == "unsupported":
                self._update_capabilities(chat_id, chat_actions=False)
            LOGGER.warning(
                "typing_started_failed",
                extra=_log_extra(
                    chat_id=chat_id,
                    thread_id=thread_id,
                    classification=exc.classification,
                    retry_after=exc.retry_after,
                    ignored=exc.fallback_allowed,
                    final_delivery_risk=exc.final_delivery_risk,
                ),
            )
            if exc.fallback_allowed:
                return False
            raise
        LOGGER.info("typing_started", extra=_log_extra(chat_id=chat_id, thread_id=thread_id))
        return True

    async def send_message_reaction(
        self,
        chat_id: int,
        message_id: int,
        emoji: str = "👍",
        *,
        is_big: bool = False,
    ) -> bool:
        if not self.capabilities_for(chat_id).message_reactions:
            LOGGER.debug("reaction_skipped", extra=_log_extra(chat_id=chat_id, message_id=message_id, reason="capability_disabled"))
            return False
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": json.dumps([{"type": "emoji", "emoji": emoji}]),
            "is_big": json.dumps(is_big),
        }
        try:
            await self.call("setMessageReaction", payload)
        except TelegramApiError as exc:
            if exc.classification == "unsupported":
                self._update_capabilities(chat_id, message_reactions=False)
            LOGGER.warning(
                "reaction_failed",
                extra=_log_extra(
                    chat_id=chat_id,
                    message_id=message_id,
                    emoji=emoji,
                    classification=exc.classification,
                    retry_after=exc.retry_after,
                    ignored=exc.fallback_allowed,
                    final_delivery_risk=exc.final_delivery_risk,
                ),
            )
            if exc.fallback_allowed:
                return False
            raise
        LOGGER.info("reaction_sent", extra=_log_extra(chat_id=chat_id, message_id=message_id, emoji=emoji))
        return True

    async def send_or_edit_message(
        self,
        *,
        chat_id: int,
        text: str,
        thread_id: int | None = None,
        reply_to_message_id: int | None = None,
        reply_markup: str | None = None,
        edit_message_id: int | None = None,
    ) -> TelegramDeliveryResult:
        if edit_message_id is not None and self.capabilities_for(chat_id).message_edits:
            try:
                message = await self.edit_message(chat_id, edit_message_id, text, reply_markup=reply_markup)
                return TelegramDeliveryResult(mode="edit", message=message)
            except TelegramApiError as exc:
                if exc.classification == "unsupported":
                    self._update_capabilities(chat_id, message_edits=False)
                LOGGER.warning(
                    "edit_failed",
                    extra=_log_extra(
                        chat_id=chat_id,
                        message_id=edit_message_id,
                        classification=exc.classification,
                        retry_after=exc.retry_after,
                        ignored=exc.fallback_allowed,
                        final_delivery_risk=exc.final_delivery_risk,
                    ),
                )
                if not exc.fallback_allowed:
                    raise
                LOGGER.info(
                    "stream_fallback_used",
                    extra=_log_extra(chat_id=chat_id, message_id=edit_message_id, fallback="sendMessage"),
                )
        message = await self.send_message(
            chat_id,
            text,
            thread_id=thread_id,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
        )
        return TelegramDeliveryResult(mode="send", message=message)

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        await self.call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    async def create_forum_topic(self, chat_id: int, name: str) -> dict:
        return await self.call("createForumTopic", {"chat_id": chat_id, "name": name})
