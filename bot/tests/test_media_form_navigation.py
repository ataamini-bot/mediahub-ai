"""Route real Telegram updates through the production router order."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, User, Voice

from app import main, navigation, runtime_config
from app.handlers import admin, home, language, media_tools
from app.services import backend
from app.state.conversion import ConversionStates
from app.state.payment import PaymentStates, AdminPaymentStates
from app.state.experience import SupportStates
from app.state.admin import AdminManagementStates
from app.state.admin_settings import AdminSettingStates
from app.state.admin_experience import AdminExperienceStates
from app.state.admin_finance import AdminFinanceStates
from app.handlers.operations import OperationStates
from app.handlers.broadcasts import BroadcastStates
from app.handlers.credit import CreditStates
from app.handlers.coupons import CouponStates
from app.handlers.backups import BackupStates
from app.handlers.customers import CustomerStates


@pytest.fixture
def ui(monkeypatch, tmp_path):
    bot = Bot("123456789:test-token")
    user = User(id=42, first_name="Example", is_bot=False, language_code="fa")
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=bot.id, chat_id=42, user_id=42))
    profile = {"telegram_id": 42, "effective_language": "fa", "is_admin": True}
    configs = (runtime_config.fallback_configuration("fa"), runtime_config.fallback_configuration("en"))

    async def configuration(lang, **_kwargs):
        return configs[1 if lang == "en" else 0]

    monkeypatch.setattr(runtime_config, "runtime_configuration", configuration)
    monkeypatch.setattr(home, "runtime_configuration", configuration)
    monkeypatch.setattr(media_tools, "runtime_configuration", configuration)
    monkeypatch.setattr(navigation, "all_runtime_configurations", AsyncMock(return_value=configs))
    monkeypatch.setattr(backend, "get_telegram_user", AsyncMock(return_value=profile))
    for module in (home, language, main):
        monkeypatch.setattr(module, "register_telegram_user", AsyncMock(return_value=profile))
    monkeypatch.setattr(Bot, "me", AsyncMock(return_value=User(
        id=bot.id, first_name="Test bot", is_bot=True, username="MediaHubTestBot")))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    stage_link, stage_upload = AsyncMock(), AsyncMock()
    monkeypatch.setattr(media_tools, "stage_music_link", stage_link)
    monkeypatch.setattr(media_tools, "stage_upload", stage_upload)
    admission = AsyncMock(side_effect=AssertionError("Navigation submitted a music request"))
    monkeypatch.setattr(media_tools, "_payment_request", admission)
    monkeypatch.setattr(media_tools, "ROOT", tmp_path)

    def message(text=None, **values):
        return Message(message_id=10, date=datetime.now(timezone.utc),
            chat=Chat(id=42, type="private"), from_user=user, text=text, **values).as_(bot)

    async def dispatch(text=None, **values):
        return await main.dp.propagate_event(update_type="message", event=message(text, **values),
            bot=bot, state=state, raw_state=await state.get_state())

    return SimpleNamespace(dispatch=dispatch, message=message, state=state, profile=profile,
        configs=configs, answer=answer, stage_link=stage_link, stage_upload=stage_upload, admission=admission)


@pytest.mark.asyncio
@pytest.mark.parametrize("current_state", [media_tools.ToolStates.upload, media_tools.ToolStates.working,
                                         ConversionStates.waiting_for_media])
@pytest.mark.parametrize("command", ["/menu", "/منو", "/menu@MediaHubTestBot"])
async def test_menu_escapes_media_forms_and_restores_bottom_keyboard(ui, current_state, command):
    await ui.state.set_state(current_state)
    await ui.state.update_data(tool_kind="music", tool_owner=42)
    await ui.dispatch(command)
    assert await ui.state.get_state() is None and await ui.state.get_data() == {}
    assert ui.answer.await_args.kwargs["reply_markup"].is_persistent
    ui.stage_link.assert_not_awaited()
    ui.admission.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("current_state", [
    None, "LegacyForm:unknown_step",
    PaymentStates.waiting_for_coupon, PaymentStates.waiting_for_receipt,
    PaymentStates.waiting_for_txid, PaymentStates.confirming_offer,
    AdminPaymentStates.waiting_for_rejection_reason, AdminPaymentStates.confirming_approval,
    SupportStates.waiting_for_user_message, SupportStates.waiting_for_user_reply,
    SupportStates.waiting_for_admin_reply, SupportStates.waiting_for_assignment,
    AdminManagementStates.waiting_for_plan_name, AdminManagementStates.confirming_plan_update,
    AdminSettingStates.waiting_for_value, AdminExperienceStates.waiting_for_copy_value,
    AdminFinanceStates.waiting_for_card_number, AdminFinanceStates.confirming_action,
    OperationStates.value, BroadcastStates.content, BroadcastStates.confirming,
    CreditStates.amount, CreditStates.confirm, CouponStates.field,
    BackupStates.confirm, CustomerStates.export_id,
])
async def test_menu_precedes_every_section_input_and_confirmation(ui, monkeypatch, current_state):
    async def never_enter_form(*_args, **_kwargs):
        pytest.fail("A section consumed /menu as form input")
    # Keep every real filter and router order, but fail immediately if any
    # child handler wins. No domain writes or external calls can be made.
    def guard(router):
        for handler in router.message.handlers:
            monkeypatch.setattr(handler, "callback", never_enter_form)
        for child in router.sub_routers:
            guard(child)
    for child in main.dp.sub_routers:
        guard(child)
    await ui.state.set_state(current_state)
    await ui.state.update_data(form_value="unfinished", confirmation_token="example")
    await ui.dispatch("/menu")
    assert await ui.state.get_state() is None and await ui.state.get_data() == {}
    ui.answer.assert_awaited_once()
    assert ui.answer.await_args.kwargs["reply_markup"].is_persistent
    ui.admission.assert_not_awaited()


@pytest.mark.asyncio
async def test_menu_clears_form_before_account_lookup_and_handles_backend_failure(ui, monkeypatch):
    async def unavailable(_message):
        assert await ui.state.get_state() is None
        assert await ui.state.get_data() == {}
        raise OSError("Backend unavailable")
    monkeypatch.setattr(home, "register_telegram_user", unavailable)
    await ui.state.set_state(PaymentStates.waiting_for_receipt)
    await ui.state.update_data(order_id=7)
    await ui.dispatch("/منو")
    assert ui.answer.await_args.kwargs["reply_markup"].is_persistent
    assert not any("Admin panel" in b.text or "پنل مدیریت" in b.text
                   for row in ui.answer.await_args.kwargs["reply_markup"].keyboard for b in row)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/language", "/admin"])
async def test_other_commands_reach_their_handlers_from_music(ui, monkeypatch, command):
    panel = AsyncMock()
    async def open_panel(message, state, **_kwargs):
        await state.clear()
        await panel(message)
    monkeypatch.setattr(admin, "show_admin_panel_message", open_panel)
    await ui.state.set_state(media_tools.ToolStates.upload)
    await ui.dispatch(command)
    assert await ui.state.get_state() is None
    ui.stage_link.assert_not_awaited()
    ui.admission.assert_not_awaited()
    if command == "/admin":
        panel.assert_awaited_once()
    else:
        assert any(b.callback_data.startswith("language:set:")
                   for row in ui.answer.await_args.kwargs["reply_markup"].inline_keyboard for b in row)


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["fa", "en"])
@pytest.mark.parametrize("action,target", [("buy", "send_payment_offers_menu"),
    ("subscription", "send_subscription_status"), ("support", "send_support_menu"),
    ("convert", "send_conversion_prompt"), ("custom", "perform_custom_button")])
async def test_renamed_and_custom_buttons_leave_music(ui, monkeypatch, lang, action, target):
    ui.profile["effective_language"] = lang
    label = "گزینهٔ تازه" if lang == "fa" else "Renamed option"
    config = ui.configs[1 if lang == "en" else 0]
    if action == "custom":
        config["custom_buttons"] = [{"id": 1, f"label_{lang}": label, "action_type": "message", "action_value": "Example"}]
    else:
        config["buttons"][action] = label
    destination = AsyncMock()
    # Conversion owns clearing its input state; use the real prompt for that route.
    if action != "convert":
        monkeypatch.setattr(home, target, destination)
    await ui.state.set_state(media_tools.ToolStates.upload)
    await ui.state.update_data(tool_kind="music", tool_owner=42)
    await ui.dispatch(label)
    assert await ui.state.get_state() == (ConversionStates.waiting_for_media.state if action == "convert" else None)
    ui.stage_link.assert_not_awaited()
    ui.admission.assert_not_awaited()
    if action != "convert":
        destination.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("queued", [False, True])
async def test_exit_cleans_only_unsubmitted_music_input(ui, queued):
    request_id = uuid4().hex
    folder = media_tools.tool_folder(42, request_id)
    folder.mkdir()
    source = folder / "input"
    source.write_bytes(b"synthetic clip")
    await ui.state.set_state(media_tools.ToolStates.upload)
    await ui.state.update_data(tool_request_id=request_id, tool_owner=42, tool_kind="music", tool_queued=queued)
    await ui.dispatch("/menu")
    assert source.exists() is queued
    assert await ui.state.get_data() == {}


@pytest.mark.asyncio
async def test_link_and_voice_still_reach_music_inputs(ui):
    await ui.state.set_state(media_tools.ToolStates.upload)
    await ui.dispatch("https://www.instagram.com/reel/Example/")
    ui.stage_link.assert_awaited_once()
    await ui.dispatch(voice=Voice(file_id="example", file_unique_id="example", duration=12, file_size=1000))
    ui.stage_upload.assert_awaited_once()
    ui.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_finished_music_work_does_not_reenter_form_after_menu_exit(ui, monkeypatch):
    request_id = uuid4().hex
    entered, finish = asyncio.Event(), asyncio.Event()
    async def wait(*_args, **_kwargs):
        entered.set()
        await finish.wait()
    monkeypatch.setattr(media_tools, "access", AsyncMock(return_value=True))
    post = AsyncMock(return_value={"id": request_id})
    monkeypatch.setattr(media_tools, "_payment_request", post)
    monkeypatch.setattr(media_tools, "wait_result", wait)
    await ui.state.set_state(media_tools.ToolStates.upload)
    await ui.state.update_data(tool_request_id=request_id, tool_kind="music", tool_owner=42,
                               tool_source_url="https://www.youtube.com/watch?v=AAAAAAAAAAA")
    task = asyncio.create_task(media_tools.submit_tool(ui.message(), 42, ui.state, request_id))
    await entered.wait()
    try:
        await ui.dispatch("/منو")
    finally:
        finish.set()
        await task
    assert await ui.state.get_state() is None and await ui.state.get_data() == {}
    post.assert_awaited_once()
