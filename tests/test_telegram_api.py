from __future__ import annotations

import unittest

from codex_telegram_gateway.telegram_api import TelegramApi, TelegramApiError


class FakeTelegramApi(TelegramApi):
    def __init__(self) -> None:
        super().__init__("token", "https://api.telegram.org")
        self.calls: list[tuple[str, dict]] = []
        self.failures: dict[str, TelegramApiError] = {}

    async def call(self, method: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        self.calls.append((method, payload))
        failure = self.failures.get(method)
        if failure is not None:
            raise failure
        if method == "sendMessage":
            return {"message_id": 99}
        if method == "editMessageText":
            return {"message_id": int(payload["message_id"])}
        return {"ok": True}


class TelegramApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_message_reaction_disables_capability_on_unsupported_error(self) -> None:
        api = FakeTelegramApi()
        api.failures["setMessageReaction"] = TelegramApiError(
            "unsupported",
            method="setMessageReaction",
            classification="unsupported",
            fallback_allowed=True,
            final_delivery_risk=False,
        )

        sent = await api.send_message_reaction(1, 55)

        self.assertFalse(sent)
        self.assertFalse(api.capabilities_for(1).message_reactions)

    async def test_send_typing_action_disables_capability_on_unsupported_error(self) -> None:
        api = FakeTelegramApi()
        api.failures["sendChatAction"] = TelegramApiError(
            "unsupported",
            method="sendChatAction",
            classification="unsupported",
            fallback_allowed=True,
            final_delivery_risk=False,
        )

        sent = await api.send_typing_action(1)

        self.assertFalse(sent)
        self.assertFalse(api.capabilities_for(1).chat_actions)

    async def test_send_or_edit_message_falls_back_to_send_on_edit_conflict(self) -> None:
        api = FakeTelegramApi()
        api.failures["editMessageText"] = TelegramApiError(
            "edit conflict",
            method="editMessageText",
            classification="edit_conflict",
            fallback_allowed=True,
            final_delivery_risk=False,
        )

        result = await api.send_or_edit_message(
            chat_id=1,
            text="hello",
            thread_id=None,
            edit_message_id=10,
        )

        self.assertEqual(result.mode, "send")
        self.assertEqual(result.message["message_id"], 99)
        self.assertEqual([method for method, _ in api.calls], ["editMessageText", "sendMessage"])

    async def test_send_or_edit_message_disables_edits_when_edit_is_unsupported(self) -> None:
        api = FakeTelegramApi()
        api.failures["editMessageText"] = TelegramApiError(
            "unsupported",
            method="editMessageText",
            classification="unsupported",
            fallback_allowed=True,
            final_delivery_risk=False,
        )

        await api.send_or_edit_message(chat_id=1, text="hello", edit_message_id=10)

        self.assertFalse(api.capabilities_for(1).message_edits)

    async def test_send_or_edit_message_uses_edit_path_when_available(self) -> None:
        api = FakeTelegramApi()

        result = await api.send_or_edit_message(chat_id=1, text="hello", edit_message_id=10)

        self.assertEqual(result.mode, "edit")
        self.assertEqual(result.message["message_id"], 10)


if __name__ == "__main__":
    unittest.main()
