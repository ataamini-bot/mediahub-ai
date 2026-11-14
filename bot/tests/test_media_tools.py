import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.handlers import media_tools as tools
from app.i18n import home_action_for_text
from app.keyboards.payment import build_home_keyboard, build_home_reply_keyboard
from app.middleware.interface import ui_language
from app.services.backend import BackendAPIError


def state():
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=42, user_id=42))


def callback(data):
    message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock(), answer_document=AsyncMock())
    return SimpleNamespace(from_user=SimpleNamespace(id=42), message=message, data=data, answer=AsyncMock())


@pytest.mark.parametrize("language", ["fa", "en"])
def test_tools_are_localized_and_follow_conversion_in_main_menu(language):
    buttons = [b for r in build_home_reply_keyboard(language).keyboard for b in r]
    actions = [home_action_for_text(b.text) for b in buttons]
    position = actions.index("convert")
    assert actions[position:position+3] == ["convert", "music", "portrait"]
    inline = [b.callback_data for r in build_home_keyboard(language).inline_keyboard for b in r]
    assert "tool:open:music" in inline and "tool:open:portrait" in inline


def test_cleanup_unsubmitted_input_preserves_handed_off_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "ROOT", tmp_path)
    request_id = uuid4().hex
    path = tools.tool_folder(42, request_id)
    path.mkdir()
    (path / "input").write_bytes(b"photo")
    data = {"tool_request_id": request_id, "tool_owner": 42, "tool_queued": True}
    tools.cleanup_tool_state(data)
    assert (path / "input").exists()
    tools.cleanup_tool_state({**data, "tool_queued": False})
    assert not path.exists()


def test_confirmation_discloses_provider_and_normal_clothing(monkeypatch):
    async def exercise():
        context = state()
        request_id = uuid4().hex
        await context.update_data(tool_kind="portrait", tool_clothing="natural", tool_background="blue")
        message = SimpleNamespace(answer=AsyncMock())
        token = ui_language.set("en")
        try:
            await tools.confirmation(message, context, request_id)
        finally:
            ui_language.reset(token)
        body = message.answer.await_args.args[0]
        assert "OpenAI" in body and "Keep my clothing" in body and "Light blue" in body
        assert "external provider" in body and "official documents" in body
        callbacks = [b.callback_data for r in message.answer.await_args.kwargs["reply_markup"].inline_keyboard for b in r]
        assert all(len(v.encode()) <= 64 for v in callbacks)
    asyncio.run(exercise())


def test_duplicate_confirmation_reuses_durable_request_id(monkeypatch):
    async def exercise():
        context = state()
        request_id = uuid4().hex
        await context.update_data(tool_request_id=request_id, tool_kind="music", tool_owner=42)
        event = callback(f"tool:start:{request_id}")
        post = AsyncMock(return_value={"id": request_id, "status": "pending"})
        monkeypatch.setattr(tools, "_payment_request", post)
        monkeypatch.setattr(tools, "access", AsyncMock(return_value=True))
        monkeypatch.setattr(tools, "wait_result", AsyncMock())
        await tools.start_tool(event, context)
        await tools.start_tool(event, context)
        assert len(post.await_args_list) == 2
        assert {call.kwargs["payload"]["request_id"] for call in post.await_args_list} == {request_id}
        assert (await context.get_data())["tool_queued"] is True
    asyncio.run(exercise())


def test_unknown_submit_outcome_keeps_file_for_worker(monkeypatch, tmp_path):
    async def exercise():
        monkeypatch.setattr(tools, "ROOT", tmp_path)
        context = state()
        request_id = uuid4().hex
        path = tools.tool_folder(42, request_id)
        path.mkdir()
        (path / "input").write_bytes(b"sample")
        await context.update_data(tool_request_id=request_id, tool_kind="music", tool_owner=42)
        monkeypatch.setattr(tools, "access", AsyncMock(return_value=True))
        monkeypatch.setattr(tools, "_payment_request", AsyncMock(side_effect=BackendAPIError(
            status_code=503, detail={"code": "backend_unavailable"})))
        await tools.start_tool(callback(f"tool:start:{request_id}"), context)
        tools.cleanup_tool_state(await context.get_data())
        assert (path / "input").exists()
    asyncio.run(exercise())


def test_no_full_source_never_sends_clip_audio(monkeypatch):
    async def exercise():
        event = callback("")
        post = AsyncMock()
        monkeypatch.setattr(tools, "_payment_request", post)
        await tools.show_result(event.message, 42, uuid4().hex, {"status": "completed", "kind": "music",
            "result": {"title": "Song", "artist": "Artist", "candidates": []}})
        assert event.message.answer.await_count == 1
        event.message.answer_document.assert_not_awaited()
        post.assert_not_awaited()
    asyncio.run(exercise())


def test_portrait_duplicate_delivery_is_claimed_once(monkeypatch, tmp_path):
    async def exercise():
        monkeypatch.setattr(tools, "ROOT", tmp_path)
        request_id = uuid4().hex
        path = tools.tool_folder(42, request_id)
        path.mkdir()
        (path / "result.jpg").write_bytes(b"photo")
        row = {"status": "completed", "kind": "portrait", "files_removed": False}
        post = AsyncMock(side_effect=[{"claimed": True}, {"released": True}])
        monkeypatch.setattr(tools, "_payment_request", post)
        event = callback("")
        await tools.show_result(event.message, 42, request_id, row)
        await tools.show_result(event.message, 42, request_id, row)
        event.message.answer_document.assert_awaited_once()
        assert not (path / "result.jpg").exists()
        assert post.await_count == 2
    asyncio.run(exercise())


def test_foreign_or_expired_confirmation_does_not_submit(monkeypatch):
    async def exercise():
        context = state()
        request_id = uuid4().hex
        await context.update_data(tool_request_id=request_id, tool_owner=7, tool_kind="music")
        post = AsyncMock()
        monkeypatch.setattr(tools, "_payment_request", post)
        await tools.start_tool(callback(f"tool:start:{request_id}"), context)
        post.assert_not_awaited()
    asyncio.run(exercise())
