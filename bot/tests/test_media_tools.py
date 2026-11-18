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
    message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock(), edit_caption=AsyncMock(),
                              answer_document=AsyncMock(), answer_video=AsyncMock(), delete=AsyncMock(), video=None)
    message.answer.return_value = message
    return SimpleNamespace(from_user=SimpleNamespace(id=42), message=message, data=data, answer=AsyncMock())


@pytest.mark.parametrize("language", ["fa", "en"])
def test_tools_are_localized_and_follow_conversion_in_main_menu(language):
    buttons = [b for r in build_home_reply_keyboard(language).keyboard for b in r]
    actions = [home_action_for_text(b.text) for b in buttons]
    position = actions.index("convert")
    assert actions[position:position+2] == ["convert", "music"]
    assert "portrait" not in actions
    inline = [b.callback_data for r in build_home_keyboard(language).inline_keyboard for b in r]
    assert "tool:open:music" in inline and "tool:open:portrait" not in inline


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
        assert event.message.edit_text.await_count == 1
        assert "Song" in event.message.edit_text.await_args.args[0]
        event.message.answer_document.assert_not_awaited()
        post.assert_not_awaited()
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


def test_music_link_submits_immediately_without_a_confirmation_message(monkeypatch):
    async def exercise():
        context = state()
        await context.update_data(tool_kind="music", tool_owner=42)
        message = SimpleNamespace(from_user=SimpleNamespace(id=42),
            text="https://www.instagram.com/reel/Example/", entities=[], message_id=123, answer=AsyncMock())
        post = AsyncMock()
        monkeypatch.setattr(tools, "_payment_request", post)
        monkeypatch.setattr(tools, "access", AsyncMock(return_value=True))
        wait = AsyncMock()
        monkeypatch.setattr(tools, "wait_result", wait)
        await tools.receive_music_link(message, context)
        data = await context.get_data()
        assert data["tool_source_url"] == message.text and data["tool_queued"]
        assert await context.get_state() == tools.ToolStates.upload.state
        post.assert_awaited_once()
        payload = post.await_args.kwargs["payload"]
        assert payload["source_url"] == message.text and payload["auto_mp3"] is True
        message.answer.assert_awaited_once()
        assert message.answer.await_args.args[0] == tools.text('request_accepted_processing')
        assert not any(b.callback_data.startswith("tool:start:") for row in
                       message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row)
        assert wait.await_args.kwargs["reply_to"] == 123
    asyncio.run(exercise())


def test_music_link_can_come_from_telegram_text_link_entity(monkeypatch):
    async def exercise():
        context = state()
        await context.update_data(tool_kind="music", tool_owner=42)
        message = SimpleNamespace(from_user=SimpleNamespace(id=42), text="این ویدئو",
            entities=[SimpleNamespace(url="https://youtu.be/AbcDefGhI12")], answer=AsyncMock())
        monkeypatch.setattr(tools, "access", AsyncMock(return_value=True))
        monkeypatch.setattr(tools, "_payment_request", AsyncMock())
        monkeypatch.setattr(tools, "wait_result", AsyncMock())
        await tools.receive_music_link(message, context)
        assert (await context.get_data())["tool_source_url"] == "https://youtu.be/AbcDefGhI12"
    asyncio.run(exercise())


def test_two_simultaneous_links_cannot_replace_an_inflight_submission(monkeypatch):
    async def exercise():
        context = state()
        await context.update_data(tool_kind="music", tool_owner=42)
        entered, finish = asyncio.Event(), asyncio.Event()
        async def wait(*args, **kwargs):
            entered.set()
            await finish.wait()
        post = AsyncMock()
        monkeypatch.setattr(tools, "access", AsyncMock(return_value=True))
        monkeypatch.setattr(tools, "_payment_request", post)
        monkeypatch.setattr(tools, "wait_result", wait)
        first = SimpleNamespace(from_user=SimpleNamespace(id=42), text="https://youtu.be/AbcDefGhI12",
                                entities=[], answer=AsyncMock())
        second = SimpleNamespace(from_user=first.from_user, text="https://youtu.be/ABCDEFGHIJK",
                                 entities=[], answer=AsyncMock())
        task = asyncio.create_task(tools.receive_music_link(first, context))
        await entered.wait()
        try:
            await tools.receive_music_link(second, context)
            post.assert_awaited_once()
            assert (await context.get_data())["tool_source_url"] == first.text
            assert second.answer.await_args.args[0] == tools.text('the_previous_upload_is_still')
            # Leaving music while the result is awaited must preserve the new form.
            await context.clear()
            await context.update_data(other_form=True)
        finally:
            finish.set()
            await task
        assert await context.get_state() is None
        assert await context.get_data() == {"other_form": True}
        assert not tools._uploads_in_progress
    asyncio.run(exercise())


def test_cancellation_during_membership_check_prevents_automatic_submission(monkeypatch):
    async def exercise():
        context = state()
        await context.update_data(tool_kind="music", tool_owner=42)
        async def access(*args, **kwargs):
            await context.clear()
            return True
        post = AsyncMock()
        monkeypatch.setattr(tools, "access", access)
        monkeypatch.setattr(tools, "_payment_request", post)
        message = SimpleNamespace(from_user=SimpleNamespace(id=42), text="https://youtu.be/AbcDefGhI12",
                                  entities=[], answer=AsyncMock())
        await tools.receive_music_link(message, context)
        post.assert_not_awaited()
        assert await context.get_data() == {}
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
        monkeypatch.setattr(tools, "access", AsyncMock(return_value=True))
        post = AsyncMock()
        monkeypatch.setattr(tools, "_payment_request", post)
        monkeypatch.setattr(tools, "wait_result", AsyncMock())
        message = SimpleNamespace(from_user=SimpleNamespace(id=42), bot=object(), answer=AsyncMock())
        await tools.stage_upload(message, context)
        data = await context.get_data()
        assert data["tool_source_url"] is None
        assert (tools.tool_folder(42, data["tool_request_id"]) / "input").read_bytes() == b"audio"
        assert data["tool_queued"] and "source_url" not in post.await_args.kwargs["payload"]
        assert message.answer.await_args.args[0] == tools.text('request_accepted_processing')
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
        # Automatic results display identity, not an extra source-selection form.
        assert "Tap Download" not in message.edit_text.await_args.args[0]
        assert message.edit_text.await_args.kwargs.get("reply_markup") is None
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
        assert "Tap Download" in message.edit_text.await_args.args[0]
        buttons = [b for r in message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for b in r]
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
        if isinstance(failure, asyncio.TimeoutError):
            assert message.answer.await_count == 2
            body = message.answer.await_args.args[0]
        else:
            assert message.answer.await_count == 1
            body = message.edit_text.await_args.args[0]
            assert all(not b.callback_data.startswith('download_pause:')
                       for r in message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in r)
        assert "secret" not in body
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
        assert message.edit_text.await_args.args[0] == tools.friendly_error("delivery_started")
    asyncio.run(exercise())


def test_back_to_sources_after_auto_download_failure_does_not_restart_download(monkeypatch):
    async def exercise():
        request_id = uuid4().hex
        event = callback(f"tool:sources:{request_id}")
        post = AsyncMock(return_value=song_result())
        monkeypatch.setattr(tools, "_payment_request", post)
        await tools.check_result(event)
        post.assert_awaited_once_with("GET", f"/media-tools/{request_id}?telegram_id=42")
        buttons = [b for row in event.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
        assert any(b.callback_data == f"tool:audio:{request_id}:0:mp3" for b in buttons)
    asyncio.run(exercise())


@pytest.mark.parametrize("language", ["fa", "en"])
def test_no_match_edits_preview_and_offers_real_help(language):
    async def exercise():
        message = callback("").message
        message.video = object()
        token = ui_language.set(language)
        try:
            await tools.show_result(message, 42, uuid4().hex,
                {"status": "failed", "error_code": "music_not_found"})
        finally:
            ui_language.reset(token)
        message.edit_caption.assert_awaited_once()
        sent = message.edit_caption.await_args.kwargs
        assert {b.callback_data for r in sent["reply_markup"].inline_keyboard for b in r} == {
            "tool:why", "tool:open:music", "support:open"}
        assert not any(word in sent["caption"] for word in ("AudD", "Shazam", "FFmpeg"))
        message.answer.assert_not_awaited()
    asyncio.run(exercise())


@pytest.mark.parametrize("send_fails", [False, True])
def test_preview_claim_cleanup_and_uncertain_send_do_not_repeat(monkeypatch, tmp_path, send_fails):
    async def exercise():
        monkeypatch.setattr(tools, "ROOT", tmp_path)
        request_id = uuid4().hex
        path = tools.tool_folder(42, request_id)
        path.mkdir()
        source = path / "input"
        source.write_bytes(b"recognition is still using this")
        preview = path / "preview.mp4"
        preview.write_bytes(b"video")
        metadata = {"width": 480, "height": 270, "duration": 12}
        post = AsyncMock(side_effect=[{"claimed": True, "preview": metadata}, {"released": True}, {"claimed": False}])
        monkeypatch.setattr(tools, "_payment_request", post)
        message = callback("").message
        if send_fails:
            message.answer_video.side_effect = TimeoutError("unknown Telegram outcome")
        else:
            message.answer_video.return_value = callback("").message
        row = {"status": "processing", "result": {"preview": metadata}}
        result = await tools.maybe_show_preview(message, 42, request_id, row, reply_to=123)
        assert result is (message if send_fails else message.answer_video.return_value)
        assert message.answer_video.await_args.kwargs["reply_parameters"].message_id == 123
        assert not preview.exists() and source.exists()
        await tools.maybe_show_preview(message, 42, request_id, row)
        message.answer_video.assert_awaited_once()
    asyncio.run(exercise())


def test_403_inside_ffmpeg_error_is_presented_as_source_access_failure(monkeypatch):
    async def exercise():
        message = callback("").message
        monkeypatch.setattr(tools, "access", AsyncMock(return_value=True))
        monkeypatch.setattr(tools, "_payment_request", AsyncMock(return_value={"id": 44}))
        monkeypatch.setattr(tools, "_download_runtime_function", {"wait_for_download":
            AsyncMock(side_effect=RuntimeError("FFmpeg exited: HTTP Error 403 secret")),
            "send_downloaded_file": AsyncMock()}.__getitem__)
        await tools.run_track_download(message, 42, uuid4().hex, 0, "mp3", row=song_result())
        body = message.edit_text.await_args.args[0]
        assert tools.text('the_song_was_identified_but_alt_alt') in body
        assert "secret" not in body
    asyncio.run(exercise())


def test_explicit_retry_uses_existing_recognition_and_sends_the_expected_failed_job(monkeypatch):
    async def exercise():
        request_id = uuid4().hex
        event = callback(f"tool:retry:{request_id}:0:mp3:44")
        monkeypatch.setattr(tools, "access", AsyncMock(return_value=True))
        post = AsyncMock(side_effect=[song_result(), {"id": 45}])
        monkeypatch.setattr(tools, "_payment_request", post)
        monkeypatch.setattr(tools, "_download_runtime_function", {"wait_for_download":
            AsyncMock(return_value={"status": "cancelled"}),
            "send_downloaded_file": AsyncMock()}.__getitem__)
        await tools.download_track(event)
        assert post.await_args.kwargs["payload"] == {"telegram_id": 42, "candidate": 0,
            "output_format": "mp3", "automatic": False, "retry_failed_job_id": 44}
        assert all(call.args[1] != "/media-tools" for call in post.await_args_list)
    asyncio.run(exercise())
