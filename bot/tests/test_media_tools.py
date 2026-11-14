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
        assert "OpenAI" not in body and "Keep my clothing" in body and "Light blue" in body
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


def test_original_cancel_button_cancels_current_queued_request(monkeypatch):
    async def exercise():
        context = state()
        request_id = uuid4().hex
        await context.update_data(tool_request_id=request_id, tool_owner=42, tool_queued=True)
        post = AsyncMock(return_value={"status": "cancelled"})
        monkeypatch.setattr(tools, "_payment_request", post)
        await tools.cancel_tool(callback("tool:cancel:"), context)
        post.assert_awaited_once_with("POST", f"/media-tools/{request_id}/cancel", payload={"telegram_id": 42})
        assert await context.get_state() is None
    asyncio.run(exercise())


def song_result(*, automatic=True):
    return {"status": "completed", "kind": "music", "auto_mp3": automatic, "result": {
        "title": "Original Song", "artist": "Example Artist", "duration": 180,
        "automatic_candidate": 0, "candidates": [{"title": "Example Artist Original Song",
            "channel": "Example Artist", "duration": 180, "url": "https://www.youtube.com/watch?v=AbcDefGhI12"}]}}


def test_music_link_is_staged_without_downloading_or_calling_provider(monkeypatch):
    async def exercise():
        context = state()
        await context.update_data(tool_kind="music", tool_owner=42)
        message = SimpleNamespace(from_user=SimpleNamespace(id=42),
            text="https://www.instagram.com/reel/Example/", entities=[], answer=AsyncMock())
        post = AsyncMock()
        monkeypatch.setattr(tools, "_payment_request", post)
        await tools.receive_music_link(message, context)
        data = await context.get_data()
        assert data["tool_source_url"] == message.text and not data["tool_queued"]
        assert await context.get_state() == tools.ToolStates.confirmation.state
        assert "AudD" not in message.answer.await_args.args[0]
        assert "سرویس بیرونی" in message.answer.await_args.args[0]
        post.assert_not_awaited()
        monkeypatch.setattr(tools, "access", AsyncMock(return_value=True))
        monkeypatch.setattr(tools, "wait_result", AsyncMock())
        await tools.start_tool(callback(f"tool:start:{data['tool_request_id']}"), context)
        payload = post.await_args.kwargs["payload"]
        assert payload["source_url"] == message.text and payload["auto_mp3"] is True
    asyncio.run(exercise())


def test_music_link_can_come_from_telegram_text_link_entity(monkeypatch):
    async def exercise():
        context = state()
        await context.update_data(tool_kind="music", tool_owner=42)
        message = SimpleNamespace(from_user=SimpleNamespace(id=42), text="این ویدئو",
            entities=[SimpleNamespace(url="https://youtu.be/AbcDefGhI12")], answer=AsyncMock())
        await tools.receive_music_link(message, context)
        assert (await context.get_data())["tool_source_url"] == "https://youtu.be/AbcDefGhI12"
    asyncio.run(exercise())


def test_upload_replaces_a_previously_staged_link(monkeypatch, tmp_path):
    async def exercise():
        context = state()
        await context.update_data(tool_kind="music", tool_owner=42, tool_source_url="https://youtu.be/AbcDefGhI12")
        monkeypatch.setattr(tools, "ROOT", tmp_path)
        item = SimpleNamespace(file_size=5, file_id="telegram-file")
        monkeypatch.setattr(tools, "_attachment", lambda _: (item, "song.mp3", 5))
        async def download(bot, file_id, target, **kwargs):
            target.write_bytes(b"audio")
        monkeypatch.setattr(tools, "_download_telegram_file", download)
        message = SimpleNamespace(from_user=SimpleNamespace(id=42), bot=object(), answer=AsyncMock())
        await tools.stage_upload(message, context)
        data = await context.get_data()
        assert data["tool_source_url"] is None
        assert (tools.tool_folder(42, data["tool_request_id"]) / "input").read_bytes() == b"audio"
    asyncio.run(exercise())


def test_strict_match_downloads_mp3_and_delivers_without_extra_click(monkeypatch):
    async def exercise():
        request_id = uuid4().hex
        message = callback("").message
        post = AsyncMock(side_effect=[{"id": 7}, {"claimed": True}])
        wait = AsyncMock(return_value={"id": 7, "status": "completed"})
        send = AsyncMock()
        monkeypatch.setattr(tools, "_payment_request", post)
        monkeypatch.setattr(tools, "access", AsyncMock(return_value=True))
        monkeypatch.setattr(tools, "_download_runtime_function", {"wait_for_download": wait, "send_downloaded_file": send}.__getitem__)
        await tools.show_result(message, 42, request_id, song_result())
        assert post.await_args_list[0].kwargs["payload"] == {
            "telegram_id": 42, "candidate": 0, "output_format": "mp3", "automatic": True}
        assert "320" in wait.await_args.args[2]
        assert post.await_args_list[1].args[1].endswith("/claim-song-delivery")
        send.assert_awaited_once()
        assert send.await_args.kwargs["media_info"]["title"] == "Example Artist - Original Song"
        assert not tools._song_downloads_in_progress
    asyncio.run(exercise())


@pytest.mark.parametrize("automatic_candidate,consent", [(None, True), (0, False)])
def test_ambiguous_or_old_requests_require_clearly_labelled_download_button(monkeypatch, automatic_candidate, consent):
    async def exercise():
        row = song_result(automatic=consent)
        row["result"]["automatic_candidate"] = automatic_candidate
        message = callback("").message
        post = AsyncMock()
        monkeypatch.setattr(tools, "_payment_request", post)
        token = ui_language.set("en")
        try:
            await tools.show_result(message, 42, uuid4().hex, row)
        finally:
            ui_language.reset(token)
        assert "No download starts until you choose" in message.answer.await_args.args[0]
        buttons = [b for r in message.answer.await_args.kwargs["reply_markup"].inline_keyboard for b in r]
        assert "MP3 320" in buttons[0].text and buttons[0].callback_data.endswith(":0:mp3")
        assert all(len(b.callback_data.encode()) <= 64 for b in buttons)
        post.assert_not_awaited()
    asyncio.run(exercise())


@pytest.mark.parametrize("failure", [RuntimeError("HTTP 403 https://private.example/?token=secret"),
                                      RuntimeError("FFmpeg conversion failed: secret"), asyncio.TimeoutError()])
def test_download_failure_does_not_leave_user_waiting_or_expose_upstream_data(monkeypatch, failure):
    async def exercise():
        request_id = uuid4().hex
        message = callback("").message
        post = AsyncMock(return_value={"id": 7})
        wait, send = AsyncMock(side_effect=failure), AsyncMock()
        monkeypatch.setattr(tools, "_payment_request", post)
        monkeypatch.setattr(tools, "access", AsyncMock(return_value=True))
        monkeypatch.setattr(tools, "_download_runtime_function", {"wait_for_download": wait, "send_downloaded_file": send}.__getitem__)
        await tools.run_track_download(message, 42, request_id, 0, "mp3", row=song_result())
        assert message.answer.await_count == 2
        assert "secret" not in message.answer.await_args.args[0]
        send.assert_not_awaited()
        assert post.await_count == 1 and not tools._song_downloads_in_progress
    asyncio.run(exercise())


def test_repeated_or_uncertain_delivery_never_sends_audio_twice(monkeypatch):
    async def exercise():
        message = callback("").message
        post = AsyncMock(side_effect=[{"id": 7}, {"claimed": False, "reason": "delivery_started"}])
        wait, send = AsyncMock(return_value={"id": 7, "status": "completed"}), AsyncMock()
        monkeypatch.setattr(tools, "_payment_request", post)
        monkeypatch.setattr(tools, "access", AsyncMock(return_value=True))
        monkeypatch.setattr(tools, "_download_runtime_function", {"wait_for_download": wait, "send_downloaded_file": send}.__getitem__)
        await tools.run_track_download(message, 42, uuid4().hex, 0, "mp3", row=song_result())
        send.assert_not_awaited()
        assert message.answer.await_args.args[0] == tools.friendly_error("delivery_started")
    asyncio.run(exercise())
