"""Exercise startup through music polling/delivery, without external services."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app import main
from app.handlers import conversion, media_tools
from app.middleware.interface import ui_language
from app.utils import telegram_cache


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "source_rejected", "encoder_failed"])
async def test_startup_wires_real_music_polling_and_delivery(monkeypatch, tmp_path, outcome):
    # Do not inject fake download helpers: startup must register the real ones.
    monkeypatch.setattr(conversion, "_download_runtime", {})
    session = SimpleNamespace(middleware=Mock(), close=AsyncMock())
    bot = SimpleNamespace(session=session)
    monkeypatch.setattr(main, "TOKEN", "123456789:test-token")
    monkeypatch.setattr(main, "AiohttpSession", Mock(return_value=session))
    monkeypatch.setattr(main, "Bot", Mock(return_value=bot))
    monkeypatch.setattr(main, "configure_telegram_menu_button", AsyncMock())
    monkeypatch.setattr(main, "run_broadcast_sender", AsyncMock())
    monkeypatch.setattr(main, "run_credit_notices", AsyncMock())
    monkeypatch.setattr(telegram_cache, "cleanup_loop", AsyncMock())
    monkeypatch.setattr(main.dp.storage, "close", AsyncMock())
    monkeypatch.setattr(main, "POLL_INTERVAL", 0)

    request_id = uuid4().hex
    job = {"id": 7, "media_type": "audio", "output_format": "mp3",
           "status": "completed" if outcome == "completed" else "failed"}
    source = tmp_path / "song.mp3"
    if outcome == "completed":
        source.write_bytes(b"synthetic-audio-delivery-fixture")
        job["file_path"] = str(source)
    else:
        job["error_message"] = (
            "ERROR: unable to download video data: HTTP Error 403: Forbidden https://private.invalid/?token=secret"
            if outcome == "source_rejected" else "FFmpeg media conversion failed: Unknown encoder secret")
    monkeypatch.setattr(main, "get_download_job", AsyncMock(side_effect=[
        {"id": 7, "status": "processing", "progress": 20}, job]))
    monkeypatch.setattr(main, "mark_download_delivered", AsyncMock())
    monkeypatch.setattr(main, "release_download_files", AsyncMock())
    monkeypatch.setattr(media_tools, "access", AsyncMock(return_value=True))
    post = AsyncMock(side_effect=[{"id": 7}, {"claimed": True}])
    monkeypatch.setattr(media_tools, "_payment_request", post)
    message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock(),
        answer_audio=AsyncMock(), answer_document=AsyncMock(), delete=AsyncMock(), video=None)
    message.answer.return_value = message
    row = {"kind": "music", "status": "completed", "result": {
        "title": "Original Song", "artist": "Example Artist",
        "candidates": [{"title": "Example Artist Original Song", "duration": 180,
                        "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA"}]}}

    async def polling(actual_bot):
        assert actual_bot is bot
        assert conversion._download_runtime_function("wait_for_download") is main.wait_for_download
        assert conversion._download_runtime_function("send_downloaded_file") is main.send_downloaded_file
        assert conversion._download_runtime_function("download_error_text") is main.download_error_text
        assert conversion._download_runtime_function("download_error_markup") is main.download_error_markup
        await media_tools.run_track_download(message, 42, request_id, 0, "mp3", row=row)
    monkeypatch.setattr(main.dp, "start_polling", AsyncMock(side_effect=polling))
    language = ui_language.set("fa")
    try:
        await main.main()
    finally:
        ui_language.reset(language)

    assert main.get_download_job.await_count == 2
    if outcome == "completed":
        message.answer_audio.assert_awaited_once()
        sent = message.answer_audio.await_args.kwargs
        assert sent["title"] == "Original Song" and sent["performer"] == "Example Artist"
        assert sent["duration"] == 180 and sent["audio"].filename.endswith(".mp3")
        assert post.await_args.args[1].endswith("/claim-song-delivery")
        main.mark_download_delivered.assert_awaited_once_with(7)
        main.release_download_files.assert_awaited_once_with(7)
        assert not source.exists()
    else:
        body = message.edit_text.await_args.args[0]
        expected = "منبع اجازهٔ دریافت فایل را نداد" if outcome == "source_rejected" else "ساخت فایل صوتی انجام نشد"
        assert expected in body and "secret" not in body and "private.invalid" not in body
        assert post.await_count == 1
        message.answer_audio.assert_not_awaited()
        main.mark_download_delivered.assert_not_awaited()
    message.answer_document.assert_not_awaited()
    session.close.assert_awaited_once()
    main.dp.storage.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_delivery_runtime_does_not_admit_an_orphan_download(monkeypatch):
    monkeypatch.setattr(conversion, "_download_runtime", {})
    monkeypatch.setattr(media_tools, "access", AsyncMock(return_value=True))
    post = AsyncMock()
    monkeypatch.setattr(media_tools, "_payment_request", post)
    message = SimpleNamespace(answer=AsyncMock())
    request_id = uuid4().hex
    row = {"kind": "music", "status": "completed", "result": {
        "title": "Original Song", "artist": "Example Artist",
        "candidates": [{"title": "Example Artist Original Song", "duration": 180,
                        "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA"}]}}
    language = ui_language.set("fa")
    try:
        await media_tools.run_track_download(message, 42, request_id, 0, "mp3", row=row)
        assert message.answer.await_args.args[0] == "❌ " + media_tools.text("the_full_track_download_failed")
    finally:
        ui_language.reset(language)
    post.assert_not_awaited()
    assert (42, request_id, 0, "mp3") not in media_tools._song_downloads_in_progress
