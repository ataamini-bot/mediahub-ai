from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendDocument

from app import main


@pytest.fixture
def delivery(monkeypatch):
    monkeypatch.setattr(main, "claim_download_delivery", AsyncMock(return_value={"claimed": True}))
    monkeypatch.setattr(main, "mark_download_delivered", AsyncMock())
    monkeypatch.setattr(main, "invalidate_cached_download", AsyncMock())
    monkeypatch.setattr(main, "release_download_files", AsyncMock())
    monkeypatch.setattr(main, "safe_edit_message", AsyncMock())
    return SimpleNamespace(answer_audio=AsyncMock(return_value=SimpleNamespace(audio=SimpleNamespace(file_id="audio-ref"))),
        answer_document=AsyncMock(return_value=SimpleNamespace(document=SimpleNamespace(file_id="document-ref"))),
        delete=AsyncMock())


def cached(kind="document"):
    return {"id": 123, "status": "completed", "file_size": 1024, "telegram_file_id": "cached-ref",
        "telegram_file_kind": kind, "telegram_filename": "Public title.mp3" if kind == "audio" else "Public title.mp4",
        "media_type": "audio" if kind == "audio" else "video", "quality": "720p", "file_path": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["audio", "document"])
async def test_reuse_sends_only_telegram_reference_with_fresh_user_buttons(delivery, tmp_path, kind):
    info = {"title": "Public title", "media_type": "video"}
    if kind == "audio":
        info["music"] = {"title": "Public song", "artist": "Example artist", "request_id": "a"*32, "candidate": 0}
    await main.send_downloaded_file(delivery, delivery, cached(kind), info)
    sender = getattr(delivery, "answer_" + kind)
    sender.assert_awaited_once()
    assert sender.await_args.kwargs[kind] == "cached-ref"
    assert sender.await_args.kwargs["reply_markup"] is not None
    main.mark_download_delivered.assert_awaited_once_with(123, {"file_id": kind + "-ref", "file_kind": kind,
        "filename": "Public title.mp3" if kind == "audio" else "Public title.mp4"})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_successful_original_upload_deletes_music_before_receipt_confirmation(delivery, tmp_path):
    path = tmp_path / "123.mp3"
    path.write_bytes(b"temporary music")
    async def confirm(*_):
        assert not path.exists()
    main.mark_download_delivered.side_effect = confirm
    job = {**cached("audio"), "file_path": str(path), "telegram_file_id": None}
    info = {"title": "Original song", "music": {"title": "Original song", "artist": "Example artist",
                                               "request_id": "a"*32, "candidate": 0}}
    await main.send_downloaded_file(delivery, delivery, job, info)
    assert not path.exists()
    assert main.mark_download_delivered.await_args.args[1]["file_id"] == "audio-ref"


@pytest.mark.asyncio
async def test_duplicate_poller_neither_sends_nor_deletes_an_inflight_upload(delivery, tmp_path):
    path = tmp_path / "123.mp4"
    path.write_bytes(b"in flight")
    main.claim_download_delivery.return_value = {"claimed": False}
    await main.send_downloaded_file(delivery, delivery, {**cached(), "file_path": str(path), "telegram_file_id": None})
    delivery.answer_document.assert_not_awaited()
    main.release_download_files.assert_not_awaited()
    assert path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("definite", [True, False])
async def test_only_definite_bad_file_reference_invalidates_cache(delivery, definite):
    error = TelegramBadRequest(method=SendDocument(chat_id=42, document="ref"), message="wrong file identifier")
    delivery.answer_document.side_effect = error if definite else TimeoutError("uncertain upload")
    with pytest.raises(RuntimeError if definite else TimeoutError):
        await main.send_downloaded_file(delivery, delivery, cached())
    main.mark_download_delivered.assert_not_awaited()
    if definite:
        main.invalidate_cached_download.assert_awaited_once_with(123)
    else:
        main.invalidate_cached_download.assert_not_awaited()
    assert delivery.answer_document.await_count == 1
