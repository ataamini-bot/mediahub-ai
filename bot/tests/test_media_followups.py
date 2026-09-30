import asyncio
from datetime import datetime, timezone
from html.parser import HTMLParser
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Document, Message, User

from app import main
from app.keyboards.download import build_completed_download_keyboard
from app.middleware.interface import ui_language
from app.services.backend import BackendAPIError
from app.state.pending import PENDING_SELECTIONS, add_pending_selection
from app.utils.filenames import media_filename


def message(**changes):
    return Message(**{"message_id": 1, "date": datetime.now(timezone.utc),
        "chat": Chat(id=42, type="private"),
        "from_user": User(id=999, is_bot=True, first_name="Bot"), **changes})


def callback(data):
    return CallbackQuery(id="cb", from_user=User(id=42, is_bot=False, first_name="User"),
        chat_instance="test", data=data, message=message(
            document=Document(file_id="old-file", file_unique_id="old", file_name="Old.mp4")))


def actions(markup):
    return {b.callback_data for row in markup.inline_keyboard for b in row}


def media_info(**changes):
    return {"title": "سفر به کوهستان", "description": "A <literal> description & text.",
        "media_type": "video", "thumbnail": "https://example.com/cover.jpg",
        "formats": [{"format_id": "720", "has_video": True, "has_audio": True,
                     "resolution": "1280x720", "extension": "mp4", "filesize": 1000}], **changes}


def job(**changes):
    return {"id": 428, "status": "completed", "source_url": "https://example.com/post",
        "media_type": "video", "playlist_index": 3, "quality": "720p", **changes}


@pytest.fixture
def ui(monkeypatch):
    language = ui_language.set("en")
    PENDING_SELECTIONS.clear()
    monkeypatch.setattr(Message, "answer", AsyncMock(return_value=message(message_id=2, text="picker")))
    monkeypatch.setattr(Message, "answer_document", AsyncMock())
    monkeypatch.setattr(Message, "edit_text", AsyncMock())
    monkeypatch.setattr(Message, "delete", AsyncMock())
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    monkeypatch.setattr(main, "get_download_job", AsyncMock(return_value=job()))
    monkeypatch.setattr(main, "get_media_info", AsyncMock(return_value=media_info()))
    monkeypatch.setattr(main, "get_download_entitlement", AsyncMock(return_value={"forced_join_required": False}))
    monkeypatch.setattr(main, "safe_edit_message", AsyncMock())
    monkeypatch.setattr(main, "mark_download_delivered", AsyncMock())
    monkeypatch.setattr(main, "release_download_files", AsyncMock())
    monkeypatch.setattr(main, "_download_cover_file", AsyncMock(side_effect=AssertionError("Unexpected cover fetch")))
    monkeypatch.setattr(main, "create_download_job", AsyncMock(return_value={"id": 429}))
    monkeypatch.setattr(main, "wait_for_download", AsyncMock(return_value={"id": 429, "status": "cancelled"}))
    yield
    PENDING_SELECTIONS.clear()
    ui_language.reset(language)


@pytest.mark.parametrize("title,suffix,expected", [
    ("سفر به کوهستان", ".mp4", "سفر به کوهستان.mp4"),
    ("Hello 🌍", ".MP3", "Hello 🌍.mp3"),
    ('../a\\b:<c>?*|\n d\u202e', ".mp4", "a b c d.mp4"),
    ("CON", ".mkv", "MediaHub-CON.mkv"),
    (None, ".mp4", "MediaHub.mp4"),
    ("clip.mp4", ".mp4", "clip.mp4"),
    ("می‌روم", ".m4a", "می‌روم.m4a"),
])
def test_user_filenames_preserve_titles_and_reject_path_control_characters(title, suffix, expected):
    assert media_filename(title, suffix) == expected


def test_long_unicode_filename_preserves_real_extension_and_stays_portable():
    name = media_filename("ویدئو🎬" * 100, ".webp", cover=True)
    assert len(name.encode("utf-8")) <= 180
    assert name.endswith(" - cover.webp")
    assert "ویدئو" in name


@pytest.mark.parametrize("language", ["fa", "en"])
@pytest.mark.parametrize("output", ["video", "audio", "cover", "description"])
def test_each_output_keeps_details_repeat_and_other_actions(language, output):
    context = ui_language.set(language)
    try:
        keyboard = build_completed_download_keyboard(428, media_type=output)
    finally:
        ui_language.reset(context)
    values = actions(keyboard)
    assert "download_details:428" in values
    repeat = f"download_more:{output}:428" if output in {"cover", "description"} else "download_again:428"
    assert repeat in values
    for other in {"video", "audio", "cover", "description"} - {output}:
        assert f"download_more:{other}:428" in values
    assert all(len(value.encode("utf-8")) <= 64 for value in values)
    labels = " ".join(b.text for row in keyboard.inline_keyboard for b in row)
    assert ("Details" in labels) == (language == "en")


def test_uploaded_conversions_and_images_do_not_offer_video_extraction():
    assert actions(build_completed_download_keyboard(428, media_type="convert")) == {
        "download_again:428", "download_details:428"}
    assert actions(build_completed_download_keyboard(428, media_type="image", source_media_type="image")) == {
        "download_again:428", "download_details:428", "download_more:description:428"}


@pytest.mark.asyncio
async def test_actual_document_filename_uses_title_and_followups_use_completed_job(ui, tmp_path):
    path = tmp_path / "internal-job-428.mp4"
    path.write_bytes(b"video")
    await main.send_downloaded_file(message(), message(), job(file_path=str(path)), media_info())
    sent = Message.answer_document.await_args.kwargs
    assert sent["document"].filename == "سفر به کوهستان.mp4"
    assert str(sent["document"].path) == str(path)
    assert "سفر به کوهستان.mp4" in sent["caption"] and "428" not in sent["caption"]
    assert "download_more:audio:428" in actions(sent["reply_markup"])
    assert not path.exists()
    main.mark_download_delivered.assert_awaited_once_with(428)


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["video", "audio"])
async def test_followup_reconstructs_source_after_restart_without_editing_file(ui, output):
    await main.download_more_callback(callback(f"download_more:{output}:428"))
    main.get_download_job.assert_awaited_once_with(428, telegram_id=42)
    main.get_media_info.assert_awaited_once_with(source_url="https://example.com/post", playlist_index=3)
    selection = next(iter(PENDING_SELECTIONS.values()))
    assert selection["playlist_index"] == 3 and selection["telegram_id"] == 42
    assert selection["source_job_id"] == 428
    assert len(Message.answer.await_args_list) == 1
    main.safe_edit_message.assert_not_awaited()
    Message.edit_text.assert_not_awaited()
    main.create_download_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_another_users_job_is_rejected_before_metadata_or_file_access(ui):
    main.get_download_job.side_effect = BackendAPIError(status_code=404, detail="Download job not found")
    await main.download_more_callback(callback("download_more:cover:428"))
    main.get_media_info.assert_not_awaited()
    Message.answer_document.assert_not_awaited()
    assert not PENDING_SELECTIONS


@pytest.mark.asyncio
async def test_required_membership_blocks_followup_without_creating_job(ui, monkeypatch):
    main.get_download_entitlement.return_value = {"forced_join_required": True}
    monkeypatch.setattr(main, "runtime_configuration", AsyncMock(return_value={}))
    monkeypatch.setattr(main, "enforce_required_membership", AsyncMock(return_value=False))
    await main.download_more_callback(callback("download_more:audio:428"))
    main.get_media_info.assert_not_awaited()
    main.create_download_job.assert_not_awaited()
    assert main.enforce_required_membership.await_args.kwargs["telegram_id"] == 42


@pytest.mark.asyncio
async def test_missing_source_does_not_consume_original_file_actions(ui):
    main.get_media_info.side_effect = RuntimeError("Source removed")
    await main.download_more_callback(callback("download_more:audio:428"))
    main.safe_edit_message.assert_not_awaited()
    main.create_download_job.assert_not_awaited()
    assert "could not be loaded" in Message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cover_has_title_filename_original_bytes_and_durable_remaining_actions(ui, tmp_path, monkeypatch):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"original-cover")
    monkeypatch.setattr(main, "_download_cover_file", AsyncMock(return_value=cover))
    await main.download_more_callback(callback("download_more:cover:428"))
    sent = Message.answer_document.await_args.kwargs
    assert sent["document"].filename == "سفر به کوهستان - cover.jpg"
    assert {"download_more:video:428", "download_more:audio:428", "download_more:description:428",
            "download_more:cover:428", "download_details:428"} == actions(sent["reply_markup"])
    assert not cover.exists()


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.value = ""
    def handle_data(self, data):
        self.value += data


@pytest.mark.asyncio
@pytest.mark.parametrize("description", ["", "<literal> & 🎬" * 500])
async def test_description_escapes_content_and_keeps_remaining_actions_on_last_chunk(ui, description):
    main.get_media_info.return_value = media_info(description=description)
    await main.download_more_callback(callback("download_more:description:428"))
    calls = Message.answer.await_args_list
    for call in calls:
        parser = VisibleText()
        parser.feed(call.args[0])
        assert len(parser.value.encode("utf-16-le")) // 2 < 4096
    assert {"download_more:video:428", "download_more:audio:428", "download_more:cover:428"} <= actions(calls[-1].kwargs["reply_markup"])
    assert all(call.kwargs.get("reply_markup") is None for call in calls[:-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["audio", "video"])
async def test_picker_keeps_same_playlist_item_and_creates_only_one_job(ui, output):
    await main.download_more_callback(callback(f"download_more:{output}:428"))
    token = next(iter(PENDING_SELECTIONS))
    data = f"audio:format:{token}:mp3" if output == "audio" else f"quality:{token}:720"
    handler = main.audio_format_callback if output == "audio" else main.quality_callback
    choice = callback(data).model_copy(update={"message": message(message_id=2, text="picker")})
    await asyncio.gather(handler(choice), handler(choice))
    main.create_download_job.assert_awaited_once()
    payload = main.create_download_job.await_args.kwargs
    assert payload["source_url"] == "https://example.com/post" and payload["playlist_index"] == 3
    assert payload["telegram_id"] == 42
    assert token not in PENDING_SELECTIONS


@pytest.mark.asyncio
async def test_selector_cannot_be_consumed_by_another_user(ui):
    token = add_pending_selection("https://example.com/post", media_info=media_info(), telegram_id=43)
    await main.audio_format_callback(callback(f"audio:format:{token}:mp3"))
    await main.quality_callback(callback(f"quality:{token}:720"))
    main.create_download_job.assert_not_awaited()
    assert token in PENDING_SELECTIONS


@pytest.mark.asyncio
async def test_cover_first_still_allows_audio_and_quality_selection(ui):
    main.get_media_info.return_value = media_info(thumbnail=None)
    token = add_pending_selection("https://example.com/post", media_info=media_info(thumbnail=None), telegram_id=42)
    await main.media_cover_callback(callback(f"media:cover:{token}"))
    assert {f"media:video:{token}", f"audio:open:{token}", f"media:description:{token}",
            f"media:details:{token}"} <= actions(Message.answer.await_args.kwargs["reply_markup"])
    await main.media_video_callback(callback(f"media:video:{token}"))
    assert any(action.startswith(f"quality:{token}:") for action in actions(Message.answer.await_args.kwargs["reply_markup"]))


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['fa', 'en'])
async def test_file_is_deleted_before_delivery_ack_and_notice_only_mentions_media(ui, tmp_path, language):
    context = ui_language.set(language)
    path = tmp_path / '428.mp4'; path.write_bytes(b'video')
    async def acknowledge(job_id):
        assert not path.exists()
    main.mark_download_delivered.side_effect = acknowledge
    try:
        await main.send_downloaded_file(message(), message(), job(file_path=str(path)), media_info())
        caption = Message.answer_document.await_args.kwargs['caption']
        assert ('media files' in caption) if language=='en' else ('فایل‌های رسانه‌ای' in caption)
        assert 'تمامی داده' not in caption and 'all data' not in caption.lower()
        assert 'https://example.com/post' not in caption
        main.release_download_files.assert_awaited_once_with(428)
    finally:
        ui_language.reset(context)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['upload', 'empty', 'delivery_ack', 'cleanup_endpoint'])
async def test_files_are_removed_on_failure_and_related_buttons_keep_job_context(ui, tmp_path, monkeypatch, failure):
    path = tmp_path / '428.mp4'; path.write_bytes(b'' if failure=='empty' else b'video')
    monkeypatch.setattr(main.asyncio, 'sleep', AsyncMock())
    if failure=='upload':
        Message.answer_document.side_effect = RuntimeError('Telegram unreachable')
    if failure=='delivery_ack':
        main.mark_download_delivered.side_effect = RuntimeError('Backend unreachable')
    if failure=='cleanup_endpoint':
        main.release_download_files.side_effect = RuntimeError('Backend unreachable')
    if failure in {'upload','empty'}:
        with pytest.raises(RuntimeError):
            await main.send_downloaded_file(message(), message(), job(file_path=str(path)), media_info())
        main.mark_download_delivered.assert_not_awaited()
    else:
        await main.send_downloaded_file(message(), message(), job(file_path=str(path)), media_info())
        markup = Message.answer_document.await_args.kwargs['reply_markup']
        assert {'download_again:428', 'download_details:428', 'download_more:audio:428'} <= actions(markup)
    assert not path.exists()
    main.release_download_files.assert_awaited_once_with(428)


def test_telegram_cache_deletion_excludes_session_files_and_other_bots(tmp_path, monkeypatch):
    from app.utils.telegram_cache import release_cached_file
    monkeypatch.setenv('TELEGRAM_BOT_API_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '123:test-cache')
    media = tmp_path/'123:test-cache'/'documents'/'file.mp4'
    media.parent.mkdir(parents=True); media.write_bytes(b'video')
    database = tmp_path/'123:test-cache'/'td.binlog'; database.write_bytes(b'session')
    other = tmp_path/'other-token'/'documents'/'file.mp4'
    other.parent.mkdir(parents=True); other.write_bytes(b'other')
    link = media.parent/'link'; link.symlink_to(database)
    for path in (media, database, other, link):
        release_cached_file(path)
    assert not media.exists() and database.exists() and other.exists() and link.exists()


@pytest.mark.asyncio
async def test_admin_activity_escapes_links_and_requires_backend_authorization(ui, monkeypatch):
    from app.handlers import customers
    monkeypatch.setattr(customers, 'api', AsyncMock(return_value={'total':1,'page':1,'items':[
        {'id':428,'status':'completed','media_type':'video','created_at':'2026-09-30T10:00:00+00:00',
         'source_url':'https://example.com/post?x=<literal>','files_removed_at':'2026-09-30T10:01:00+00:00'}]}))
    await customers.download_activity(callback('customer:downloads:42:1'))
    assert '&lt;literal&gt;' in Message.edit_text.await_args.args[0]
    markup = Message.edit_text.await_args.kwargs['reply_markup']
    assert markup.inline_keyboard[0][0].url=='https://example.com/post?x=<literal>'
    Message.edit_text.reset_mock()
    customers.api.side_effect=BackendAPIError(status_code=403,detail='Forbidden')
    await customers.download_activity(callback('customer:downloads:42:1'))
    Message.edit_text.assert_not_awaited()
    assert CallbackQuery.answer.await_args.kwargs['show_alert']
