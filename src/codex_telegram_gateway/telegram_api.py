from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TelegramApiError(RuntimeError):
    pass


class TelegramApi:
    def __init__(self, token: str, api_base: str) -> None:
        self.base_url = f"{api_base.rstrip('/')}/bot{token}"

    async def call(self, method: str, payload: dict | None = None) -> dict:
        return await asyncio.to_thread(self._call_sync, method, payload or {})

    def _call_sync(self, method: str, payload: dict) -> dict:
        data = urlencode(payload).encode("utf-8")
        request = Request(f"{self.base_url}/{method}", data=data)
        try:
            with urlopen(request, timeout=60) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise TelegramApiError(f"Telegram API request failed for {method}: {exc}") from exc
        if not parsed.get("ok"):
            raise TelegramApiError(f"Telegram API returned error for {method}: {parsed}")
        return parsed["result"]

    async def get_updates(self, offset: int | None, timeout_seconds: int) -> list[dict]:
        payload = {"timeout": timeout_seconds}
        if offset is not None:
            payload["offset"] = offset
        return await self.call("getUpdates", payload)

    async def send_message(self, chat_id: int, text: str, thread_id: int | None = None, reply_to_message_id: int | None = None, reply_markup: str | None = None) -> dict:
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

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        await self.call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})
