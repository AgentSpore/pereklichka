"""Finite technical events; Telegram payloads and exception values never reach sinks."""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramAPIError
from aiogram.types import TelegramObject, Update
from aiogram.types.update import UpdateTypeLookupError
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError


def _error_class(error: BaseException | None) -> str:
    for kind, label in (
        (TimeoutError, "timeout"),
        (TelegramAPIError, "telegram"),
        (SQLAlchemyError, "database"),
        (ValueError, "validation"),
        (OSError, "network"),
    ):
        if isinstance(error, kind):
            return label
    return "none" if error is None else "unknown"


def _emit(event: str, kind: str, outcome: str, duration: int | None, error: str) -> None:
    level = "ERROR" if outcome == "error" else "WARNING" if event != "telegram.update" else "INFO"
    logger.log(
        level,
        "{}",
        json.dumps(
            {
                "service": "pereklichka",
                "event": event,
                "update_type": kind,
                "outcome": outcome,
                "duration_ms": duration,
                "error_class": error,
            },
            separators=(",", ":"),
        ),
    )


class UpdateLogMiddleware(BaseMiddleware):
    """Observe actual handler outcomes without changing values or exception propagation."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        kind, outcome, error = "other", "error", "none"
        started = time.monotonic()
        try:
            if isinstance(event, Update):
                try:
                    update_type = event.event_type
                except UpdateTypeLookupError:
                    outcome = "unhandled"
                    return UNHANDLED
                kind = {"message": "message", "callback_query": "callback"}.get(
                    update_type, "other"
                )
            result = await handler(event, data)
            outcome = "unhandled" if result is UNHANDLED else "handled"
            return result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as failure:
            error = _error_class(failure)
            raise
        finally:
            duration = int(min(max(time.monotonic() - started, 0), 3600) * 1000)
            _emit("telegram.update", kind, outcome, duration, error)


class _FrameworkLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.WARNING:
            return False
        error = record.exc_info[1] if record.exc_info else None
        if error is None and isinstance(record.args, tuple):
            error = next((value for value in record.args if isinstance(value, BaseException)), None)
        _emit(
            "telegram.polling" if record.name == "aiogram.dispatcher" else "telegram.framework",
            "other",
            "error" if record.levelno >= logging.ERROR else "unhandled",
            None,
            _error_class(error) if error is not None else "unknown",
        )
        return False


def install_framework_logging() -> None:
    """Replace only aiogram update/polling records before any raw handler sees them."""
    for name in ("aiogram.event", "aiogram.dispatcher"):
        framework = logging.getLogger(name)
        if not any(isinstance(item, _FrameworkLogs) for item in framework.filters):
            framework.addFilter(_FrameworkLogs())
