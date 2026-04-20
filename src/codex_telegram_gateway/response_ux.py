from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .codex_adapter import PolicyEnforcementError
from .config import AppConfig
from .logging_utils import log_extra
from .models import CodexRunResult, RunEvent, TelegramRequestIdentity, TelegramResponseContext
from .telegram_api import TelegramApi, TelegramApiError
from .workspace_preflight import WorkspacePreflightError


INLINE_KEYBOARD = json.dumps(
    {
        "inline_keyboard": [
            [
                {"text": "Status", "callback_data": "status"},
                {"text": "Where", "callback_data": "where"},
                {"text": "Reset Session", "callback_data": "resetsession"},
            ]
        ]
    }
)


RunExecutor = Callable[[Callable[[RunEvent], Awaitable[None]]], Awaitable[CodexRunResult]]


@dataclass(frozen=True, slots=True)
class TelegramFeatureSupport:
    message_reactions: bool
    chat_actions: bool
    message_edits: bool


@dataclass(slots=True)
class ResponseUxLifecycle:
    context: TelegramResponseContext
    created_at: float
    current_message_id: int | None = None
    buffer_parts: list[str] = field(default_factory=list)
    last_edit_at: float = 0.0
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    typing_task: asyncio.Task[None] | None = None
    progress_stage: str | None = None
    progress_disabled: bool = False
    streaming_disabled: bool = False


class ProgressAggregator:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def consume(self, event: RunEvent) -> str | None:
        stage: str | None = None
        if event.kind == "session_started":
            stage = "Session ready"
        elif event.kind == "lifecycle":
            raw_type = event.raw_type or ""
            if raw_type == "turn.started":
                stage = "Thinking"
            elif "tool" in raw_type:
                stage = "Running tools"
            elif "exec" in raw_type or "command" in raw_type:
                stage = "Running command"
        elif event.kind == "stderr":
            stage = "Processing command output"
        elif event.kind == "error":
            stage = "Handling issue"
        if stage is not None:
            log_extra(
                self.logger,
                "progress_aggregation_decision",
                event_kind=event.kind,
                raw_type=event.raw_type,
                stage=stage,
            )
        else:
            log_extra(
                self.logger,
                "progress_event_dropped",
                event_kind=event.kind,
                raw_type=event.raw_type,
            )
        return stage


class ProgressReporter:
    def __init__(self, coordinator: "ResponseUxCoordinator") -> None:
        self.coordinator = coordinator

    async def report(self, lifecycle: ResponseUxLifecycle, stage: str) -> None:
        if lifecycle.progress_disabled:
            log_extra(
                self.coordinator.logger,
                "progress_update_skipped",
                request_id=lifecycle.context.identity.key,
                reason="disabled",
                stage=stage,
            )
            return
        if lifecycle.progress_stage == stage:
            log_extra(
                self.coordinator.logger,
                "progress_update_skipped",
                request_id=lifecycle.context.identity.key,
                reason="unchanged",
                stage=stage,
            )
            return
        lifecycle.progress_stage = stage
        preview = f"[{lifecycle.context.workspace_name}] working\n\nStage: {stage}"
        try:
            await self.coordinator._edit_or_fallback(lifecycle, preview, reply_markup=INLINE_KEYBOARD)
        except Exception:
            lifecycle.progress_disabled = True
            self.coordinator.logger.exception("progress_update_failed")
            log_extra(
                self.coordinator.logger,
                "progress_fallback_disabled",
                request_id=lifecycle.context.identity.key,
                stage=stage,
            )
            return
        log_extra(
            self.coordinator.logger,
            "progress_update_sent",
            request_id=lifecycle.context.identity.key,
            stage=stage,
        )


class StreamingResponder:
    def __init__(self, coordinator: "ResponseUxCoordinator") -> None:
        self.coordinator = coordinator

    async def consume(self, lifecycle: ResponseUxLifecycle, text: str) -> None:
        if lifecycle.streaming_disabled:
            log_extra(
                self.coordinator.logger,
                "stream_chunk_skipped",
                request_id=lifecycle.context.identity.key,
                reason="disabled",
            )
            return
        if not lifecycle.buffer_parts:
            log_extra(
                self.coordinator.logger,
                "stream_started",
                request_id=lifecycle.context.identity.key,
                chat_id=lifecycle.context.target.chat_id,
                thread_id=lifecycle.context.target.thread_id,
            )
        lifecycle.buffer_parts.append(text)
        now = asyncio.get_running_loop().time()
        if now - lifecycle.last_edit_at < self.coordinator.config.stream_edit_interval_seconds:
            log_extra(
                self.coordinator.logger,
                "stream_chunk_skipped",
                request_id=lifecycle.context.identity.key,
                reason="throttled",
            )
            return
        lifecycle.last_edit_at = now
        preview = self.coordinator._truncate_for_telegram(
            f"[{lifecycle.context.workspace_name}] running\n\n{''.join(lifecycle.buffer_parts)}"
        )
        try:
            await self.coordinator._edit_or_fallback(lifecycle, preview, reply_markup=INLINE_KEYBOARD)
        except Exception:
            lifecycle.streaming_disabled = True
            self.coordinator.logger.exception("stream_failed")
            log_extra(
                self.coordinator.logger,
                "stream_fallback_disabled",
                request_id=lifecycle.context.identity.key,
            )
            return
        log_extra(
            self.coordinator.logger,
            "stream_chunk_sent",
            request_id=lifecycle.context.identity.key,
            chunk_length=len(text),
            message_length=len(preview),
        )


class ResponseUxCoordinator:
    TYPING_HEARTBEAT_SECONDS = 4.0

    def __init__(self, config: AppConfig, telegram: TelegramApi, logger: logging.Logger) -> None:
        self.config = config
        self.telegram = telegram
        self.logger = logger
        self._lifecycles: dict[str, ResponseUxLifecycle] = {}
        self._progress_aggregator = ProgressAggregator(logger)
        self._progress_reporter = ProgressReporter(self)
        self._streaming_responder = StreamingResponder(self)

    def _feature_support(self, chat_id: int) -> TelegramFeatureSupport:
        capabilities_for = getattr(self.telegram, "capabilities_for", None)
        if capabilities_for is None:
            return TelegramFeatureSupport(message_reactions=True, chat_actions=True, message_edits=True)
        capabilities = capabilities_for(chat_id)
        return TelegramFeatureSupport(
            message_reactions=capabilities.message_reactions,
            chat_actions=capabilities.chat_actions,
            message_edits=capabilities.message_edits,
        )

    def _register(self, context: TelegramResponseContext) -> ResponseUxLifecycle | None:
        if context.identity.key in self._lifecycles:
            log_extra(self.logger, "response_ux_duplicate_request", request_id=context.identity.key, workspace=context.workspace_name)
            return None
        lifecycle = ResponseUxLifecycle(
            context=context,
            created_at=asyncio.get_running_loop().time(),
        )
        self._lifecycles[context.identity.key] = lifecycle
        support = self._feature_support(context.target.chat_id)
        log_extra(
            self.logger,
            "inbound_message_accepted",
            request_id=context.identity.key,
            chat_id=context.target.chat_id,
            thread_id=context.target.thread_id,
            workspace=context.workspace_name,
            policy_scope=context.policy.scope_name,
            reaction=context.policy.allow_reaction,
            typing=context.policy.allow_typing,
            progress=context.policy.allow_progress_updates,
            stream=context.policy.allow_streaming_text,
            final_only=context.policy.final_only,
            can_react=support.message_reactions,
            can_type=support.chat_actions,
            can_edit=support.message_edits,
        )
        return lifecycle

    async def _cleanup(self, request_id: str, *, reason: str) -> None:
        lifecycle = self._lifecycles.pop(request_id, None)
        if lifecycle is None:
            return
        await self._stop_typing_heartbeat(lifecycle, reason=reason)
        log_extra(
            self.logger,
            "response_ux_lifecycle_stop",
            request_id=request_id,
            workspace=lifecycle.context.workspace_name,
            reason=reason,
        )

    async def cancel_scope(self, chat_id: int, thread_id: int | None, *, reason: str) -> None:
        request_ids = [
            request_id
            for request_id, lifecycle in self._lifecycles.items()
            if lifecycle.context.target.chat_id == chat_id and lifecycle.context.target.thread_id == thread_id
        ]
        for request_id in request_ids:
            await self._cleanup(request_id, reason=reason)

    async def run(self, context: TelegramResponseContext, execute_run: RunExecutor) -> None:
        lifecycle = self._register(context)
        if lifecycle is None:
            return
        log_extra(
            self.logger,
            "response_ux_lifecycle_start",
            request_id=context.identity.key,
            workspace=context.workspace_name,
        )
        try:
            await self._send_initial_ack(lifecycle)
            result = await execute_run(lambda event: self._handle_event(lifecycle, event))
        except WorkspacePreflightError as exc:
            await self._edit_or_fallback(lifecycle, f"[{context.workspace_name}] {exc.result.user_message}")
            await self._cleanup(context.identity.key, reason="preflight_failed")
            return
        except PolicyEnforcementError as exc:
            await self._edit_or_fallback(lifecycle, f"[{context.workspace_name}] {exc}")
            await self._cleanup(context.identity.key, reason="policy_rejected")
            return
        except Exception:
            self.logger.exception("codex.run.crashed")
            await self._edit_or_fallback(lifecycle, f"[{context.workspace_name}] internal error")
            await self._cleanup(context.identity.key, reason="internal_error")
            return
        await self._finalize(lifecycle, result)
        await self._cleanup(context.identity.key, reason="completed" if result.ok else "failed")

    async def _send_initial_ack(self, lifecycle: ResponseUxLifecycle) -> None:
        context = lifecycle.context
        if context.policy.allow_reaction and context.target.reply_to_message_id is not None:
            await self.telegram.send_message_reaction(
                context.target.chat_id,
                context.target.reply_to_message_id,
            )
        if context.policy.allow_typing:
            await self._start_typing_heartbeat(lifecycle)

    async def _start_typing_heartbeat(self, lifecycle: ResponseUxLifecycle) -> None:
        if lifecycle.typing_task is not None:
            return
        log_extra(
            self.logger,
            "typing_heartbeat_started",
            request_id=lifecycle.context.identity.key,
            chat_id=lifecycle.context.target.chat_id,
            thread_id=lifecycle.context.target.thread_id,
        )
        lifecycle.typing_task = asyncio.create_task(self._typing_heartbeat(lifecycle))

    async def _stop_typing_heartbeat(self, lifecycle: ResponseUxLifecycle, *, reason: str) -> None:
        lifecycle.stop_event.set()
        if lifecycle.typing_task is None:
            return
        lifecycle.typing_task.cancel()
        try:
            await lifecycle.typing_task
        except asyncio.CancelledError:
            pass
        lifecycle.typing_task = None
        log_extra(
            self.logger,
            "typing_heartbeat_stopped",
            request_id=lifecycle.context.identity.key,
            chat_id=lifecycle.context.target.chat_id,
            thread_id=lifecycle.context.target.thread_id,
            reason=reason,
        )

    async def _typing_heartbeat(self, lifecycle: ResponseUxLifecycle) -> None:
        while not lifecycle.stop_event.is_set():
            sent = await self.telegram.send_typing_action(
                lifecycle.context.target.chat_id,
                lifecycle.context.target.thread_id,
            )
            if not sent:
                log_extra(
                    self.logger,
                    "typing_heartbeat_skipped",
                    request_id=lifecycle.context.identity.key,
                    chat_id=lifecycle.context.target.chat_id,
                    thread_id=lifecycle.context.target.thread_id,
                )
                return
            try:
                await asyncio.wait_for(lifecycle.stop_event.wait(), timeout=self.TYPING_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                continue

    async def _handle_event(self, lifecycle: ResponseUxLifecycle, event: RunEvent) -> None:
        if lifecycle.context.policy.allow_progress_updates:
            stage = self._progress_aggregator.consume(event)
            if stage is not None:
                await self._progress_reporter.report(lifecycle, stage)
        text = event.text
        if event.kind == "stderr" and event.text:
            text = f"[stderr] {event.text}"
        elif event.kind == "error" and event.text:
            text = f"[info] {event.text}"
        if not text:
            return
        if not lifecycle.context.policy.allow_streaming_text:
            log_extra(
                self.logger,
                "stream_chunk_skipped",
                request_id=lifecycle.context.identity.key,
                reason="policy_disabled",
            )
            return
        await self._streaming_responder.consume(lifecycle, text)

    async def _finalize(self, lifecycle: ResponseUxLifecycle, result: CodexRunResult) -> None:
        final_text = result.final_text or "(empty response)"
        summary = (
            f"[{lifecycle.context.workspace_name}] {'done' if result.ok else 'failed'} in {result.duration_seconds:.1f}s\n"
            f"Session: {result.session_id or 'n/a'}\n\n{final_text}"
        )
        chunks = self._split_for_telegram(summary)
        await self._edit_or_fallback(lifecycle, chunks[0], reply_markup=INLINE_KEYBOARD)
        for extra in chunks[1:]:
            await self.telegram.send_message(
                lifecycle.context.target.chat_id,
                extra,
                lifecycle.context.target.thread_id,
            )
        log_extra(
            self.logger,
            "final_response_sent",
            request_id=lifecycle.context.identity.key,
            chunk_count=len(chunks),
            ok=result.ok,
        )
        if result.errors and not result.ok:
            error_text = self._truncate_for_telegram("Errors:\n" + "\n".join(result.errors[-20:]))
            await self.telegram.send_message(
                lifecycle.context.target.chat_id,
                error_text,
                lifecycle.context.target.thread_id,
            )

    async def _edit_or_fallback(self, lifecycle: ResponseUxLifecycle, text: str, *, reply_markup: str | None = None) -> None:
        send_or_edit = getattr(self.telegram, "send_or_edit_message", None)
        if send_or_edit is not None:
            result = await send_or_edit(
                chat_id=lifecycle.context.target.chat_id,
                text=text,
                thread_id=lifecycle.context.target.thread_id,
                reply_to_message_id=lifecycle.context.target.reply_to_message_id,
                reply_markup=reply_markup,
                edit_message_id=lifecycle.current_message_id,
            )
            lifecycle.current_message_id = int(result.message["message_id"])
            if result.mode == "send" and lifecycle.buffer_parts:
                log_extra(
                    self.logger,
                    "stream_fallback_used",
                    request_id=lifecycle.context.identity.key,
                    fallback="send_message",
                )
            return
        if lifecycle.current_message_id is None:
            replacement = await self.telegram.send_message(
                lifecycle.context.target.chat_id,
                text,
                lifecycle.context.target.thread_id,
                reply_markup=reply_markup,
            )
            lifecycle.current_message_id = int(replacement["message_id"])
            return
        try:
            await self.telegram.edit_message(
                lifecycle.context.target.chat_id,
                lifecycle.current_message_id,
                text,
                reply_markup=reply_markup,
            )
        except TelegramApiError:
            self.logger.exception("telegram.edit.failed")
            replacement = await self.telegram.send_message(
                lifecycle.context.target.chat_id,
                text,
                lifecycle.context.target.thread_id,
                reply_markup=reply_markup,
            )
            lifecycle.current_message_id = int(replacement["message_id"])

    def _truncate_for_telegram(self, text: str) -> str:
        return text[: self.config.telegram_message_chunk]

    def _split_for_telegram(self, text: str) -> list[str]:
        size = self.config.telegram_message_chunk
        if len(text) <= size:
            return [text]
        lines = text.splitlines(keepends=True)
        chunks: list[str] = []
        current = ""
        for line in lines:
            if len(current) + len(line) > size and current:
                chunks.append(current)
                current = ""
            current += line
        if current:
            chunks.append(current)
        log_extra(
            self.logger,
            "message_length_split",
            chunk_count=len(chunks),
            total_length=len(text),
        )
        return chunks
