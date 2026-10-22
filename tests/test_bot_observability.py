"""Real dispatcher and polling boundaries with synthetic Telegram payloads only."""

import asyncio
import json
import logging
from datetime import UTC, datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from loguru import logger
from sqlalchemy.ext.asyncio import async_sessionmaker

from pereklichka.bot.router import create_dispatcher
from pereklichka.bot.runtime import run_polling

MARKER = "synthetic-private-content"


@pytest.fixture
def records(caplog):
    caplog.set_level(logging.DEBUG)
    names = ("aiogram.event", "aiogram.dispatcher")
    previous = {name: logging.getLogger(name).filters[:] for name in names}
    captured = []
    sink = logger.add(lambda message: captured.append(message), format="{message}")
    yield captured
    logger.remove(sink)
    for name in names:
        logging.getLogger(name).filters[:] = previous[name]


@pytest.fixture
def update():
    user = User(id=675849123, is_bot=False, first_name=MARKER)
    message = Message(
        message_id=123,
        date=datetime.now(UTC),
        chat=Chat(id=user.id, type="private"),
        from_user=user,
        text=MARKER,
    )
    return Update(update_id=987654321, message=message)


@pytest.mark.parametrize("case", ["message", "callback", "unhandled", "error", "cancelled"])
async def test_dispatcher_preserves_results_and_emits_safe_event(records, update, case, caplog):
    dispatcher = create_dispatcher(async_sessionmaker(), "synthetic_bot")
    result = object()
    failure = ValueError(MARKER) if case == "error" else asyncio.CancelledError(MARKER)

    async def handler(event):
        if case in {"error", "cancelled"}:
            raise failure
        return result

    if case == "callback":
        update = Update(
            update_id=987654321,
            callback_query=CallbackQuery(
                id=MARKER,
                from_user=update.message.from_user,
                chat_instance=MARKER,
                data=MARKER,
            ),
        )
        dispatcher.callback_query.register(handler)
    elif case == "unhandled":
        update = Update(update_id=987654321, edited_message=update.message)
    else:
        dispatcher.message.register(handler)
    bot = Bot("123456:" + "a" * 35)
    try:
        if case in {"error", "cancelled"}:
            with pytest.raises(type(failure)) as caught:
                await dispatcher.feed_update(bot, update)
            assert caught.value is failure
        else:
            response = await dispatcher.feed_update(bot, update)
            if case != "unhandled":
                assert response is result
    finally:
        await bot.session.close()
    assert len(records) == 1
    event = json.loads(records[0])
    assert event["event"] == "telegram.update"
    assert event["update_type"] == {"unhandled": "other", "callback": "callback"}.get(
        case, "message"
    )
    assert event["outcome"] == ("handled" if case in {"message", "callback"} else case)
    assert 0 <= event["duration_ms"] <= 3600000
    assert event["error_class"] == ("validation" if case == "error" else "none")
    assert all(
        secret not in "".join(records) + caplog.text
        for secret in [MARKER, "675849123", "987654321", "123456"]
    )


async def test_polling_exception_is_replaced_without_raw_traceback(records, update, caplog, capfd):
    dispatcher = create_dispatcher(async_sessionmaker(), "synthetic_bot")

    async def fail(message):
        raise RuntimeError(MARKER)

    dispatcher.message.register(fail)
    bot = Bot("123456:" + "a" * 35)
    try:
        assert await dispatcher._process_update(bot, update) is True
    finally:
        await bot.session.close()
    events = [json.loads(record) for record in records]
    assert any(
        event["event"] == "telegram.framework" and event["outcome"] == "error" for event in events
    )
    captured = "".join(records) + caplog.text + "".join(capfd.readouterr())
    assert all(secret not in captured for secret in [MARKER, "675849123", "987654321", "Traceback"])


async def test_polling_startup_installs_safe_framework_logging(records, monkeypatch, capfd):
    dispatcher, bot = Dispatcher(), Bot("123456:" + "a" * 35)
    attempted = asyncio.Event()

    async def request(bot, method, timeout=None):
        attempted.set()
        raise OSError(MARKER)

    bot._me = User(id=123456, is_bot=True, first_name=MARKER, username="synthetic_bot")
    monkeypatch.setattr(bot.session, "make_request", request)
    task = asyncio.create_task(run_polling(dispatcher, bot))
    try:
        await asyncio.wait_for(attempted.wait(), 2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await bot.session.close()
    events = [json.loads(record) for record in records]
    assert any(
        event["event"] == "telegram.polling" and event["error_class"] == "network"
        for event in events
    )
    captured = "".join(capfd.readouterr())
    assert {record.record["level"].name for record in records} >= {"ERROR", "WARNING"}
    assert MARKER not in "".join(records) + captured


def test_framework_filter_does_not_suppress_other_loggers(records, caplog):
    create_dispatcher(async_sessionmaker(), "synthetic_bot")
    logging.getLogger("unrelated.operational.logger").warning("unrelated-visible")
    assert "unrelated-visible" in caplog.text
    assert records == []


async def test_unknown_future_update_is_unhandled_without_payload_warning(records, recwarn, capfd):
    dispatcher = create_dispatcher(async_sessionmaker(), "synthetic_bot")
    bot = Bot("123456:" + "a" * 35)
    update = Update(update_id=987654321, future_update={"text": MARKER})
    try:
        response = await dispatcher.feed_update(bot, update)
    finally:
        await bot.session.close()
    assert response is UNHANDLED
    assert not recwarn.list
    assert len(records) == 1
    event = json.loads(records[0])
    assert event["update_type"] == "other" and event["outcome"] == "unhandled"
    captured = "".join(records) + "".join(capfd.readouterr())
    assert MARKER not in captured and "987654321" not in captured
