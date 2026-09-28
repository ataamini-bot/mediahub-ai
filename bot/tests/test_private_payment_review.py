import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Document, Message

from app.handlers import admin_finance, payments
from app.middleware.interface import ui_language
from app.services import payment_delivery
from app.state.payment import AdminPaymentStates
from tests.test_bilingual_payment_support import _payment_callback


def payment_record(status="pending", kind="photo"):
    return {
        "id": 47, "status": status, "amount": "79000", "payment_method": "card",
        "plan_name_snapshot": "Silver <plan>", "duration_days": 30,
        "receipt_file_id": "saved-receipt" if kind else None, "receipt_file_type": kind,
        "user_telegram_id": 12345, "first_name": "Customer <name>", "last_name": None,
        "reviewed_by_telegram_id": 12345, "reviewed_at": "2026-09-28T12:00:00+00:00",
    }


def action_result(status="pending", kind="photo", **flags):
    return {"payment": payment_record(status, kind),
            "user": {"telegram_id": 12345, "first_name": "Customer <name>", "effective_language": "fa"},
            "subscription": {"expires_at": "2027-01-01T00:00:00+00:00"}, **flags}


@pytest.fixture
def harness(monkeypatch):
    callback, state = _payment_callback("payment_admin:approve-confirm:47", photo=True)
    bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock(), send_document=AsyncMock())
    callback.as_(bot)
    callback.message.as_(bot)
    for name in ("answer", "reply", "edit_text", "edit_caption", "edit_reply_markup"):
        monkeypatch.setattr(Message, name, AsyncMock())
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    monkeypatch.setattr(payments, "get_admin_context", AsyncMock(return_value={
        "is_admin": True, "is_superadmin": True, "permissions": [],
    }))
    monkeypatch.setattr(payments, "_notify_user_approved", AsyncMock())
    monkeypatch.setattr(payments, "_notify_user_rejected", AsyncMock())
    routes = AsyncMock(return_value={"enabled": True, "chat_id": -100900, "topics": {"payments": 91}})
    monkeypatch.setattr(payment_delivery, "notification_routes", routes)
    return callback, state, bot, routes


@pytest.mark.parametrize("kind", ["photo", "document", None])
def test_submission_delivers_private_receipts_without_consulting_topic(harness, monkeypatch, kind):
    callback, state, bot, routes = harness
    message = callback.message
    if kind == "document":
        message = message.model_copy(update={"photo": None, "document": Document(
            file_id="pdf-id", file_unique_id="pdf-unique", mime_type="application/pdf", file_size=100,
        )}).as_(bot)
    result = action_result(kind=kind)
    create = AsyncMock(return_value=result)
    monkeypatch.setattr(payments, "create_manual_payment", create)
    monkeypatch.setattr(payments, "register_telegram_user", AsyncMock())
    monkeypatch.setattr(payments, "get_telegram_user", AsyncMock(return_value=result["user"]))
    monkeypatch.setattr(payments, "_user_home_reply_keyboard", AsyncMock(return_value=None))
    monkeypatch.setattr(payments, "list_payment_reviewers", AsyncMock(return_value=[
        {"telegram_id": 111, "language": "fa"}, {"telegram_id": 222, "language": "en"},
    ]))
    routes.side_effect = AssertionError("Submission must not depend on a Topic")

    async def exercise():
        await state.update_data(offer_code="silver", offer={"label": "Silver", "currency": "IRT"},
                                receipt_rules={"allowed_types": ["application/pdf"]})
        await payments._submit_payment_from_state(message, state, receipt_message=message if kind else None)
        assert await state.get_state() is None
    asyncio.run(exercise())
    create.assert_awaited_once()
    assert [c.kwargs["chat_id"] for c in bot.send_message.await_args_list] == [111, 222]
    for call in bot.send_message.await_args_list:
        assert "message_thread_id" not in call.kwargs
        assert "payment_admin:approve:47" in str(call.kwargs["reply_markup"])
    assert "رسید جدید" in bot.send_message.await_args_list[0].kwargs["text"]
    assert "New" in bot.send_message.await_args_list[1].kwargs["text"]
    if kind:
        assert getattr(bot, f"send_{kind}").await_count == 2
    else:
        bot.send_photo.assert_not_awaited()
        bot.send_document.assert_not_awaited()
    routes.assert_not_awaited()


def test_failed_reviewer_does_not_block_next_or_change_customer_language(harness, monkeypatch):
    callback, _, bot, _ = harness
    monkeypatch.setattr(payments, "list_payment_reviewers", AsyncMock(return_value=[
        {"telegram_id": 111, "language": "fa"}, {"telegram_id": 222, "language": "en"},
    ]))
    bot.send_message.side_effect = [RuntimeError("blocked"), None]
    token = ui_language.set("fa")
    try:
        asyncio.run(payments._notify_payment_reviewers(callback.message, action_result(), {"label": "Silver"}))
        assert ui_language.get() == "fa"
    finally:
        ui_language.reset(token)
    assert bot.send_message.await_count == 2


@pytest.mark.parametrize("delivery", ["already_submitted", "lookup_failed", "unreachable"])
def test_saved_receipt_is_acknowledged_despite_delivery_failure_or_submission_retry(harness, monkeypatch, delivery):
    callback, state, bot, routes = harness
    result = action_result(already_submitted=delivery == "already_submitted")
    monkeypatch.setattr(payments, "create_manual_payment", AsyncMock(return_value=result))
    monkeypatch.setattr(payments, "register_telegram_user", AsyncMock())
    monkeypatch.setattr(payments, "get_telegram_user", AsyncMock(return_value=result["user"]))
    monkeypatch.setattr(payments, "_user_home_reply_keyboard", AsyncMock(return_value=None))
    reviewers = AsyncMock(return_value=[{"telegram_id": 111, "language": "fa"}])
    if delivery == "lookup_failed":
        reviewers.side_effect = RuntimeError("unavailable")
    bot.send_message.side_effect = RuntimeError("blocked")
    monkeypatch.setattr(payments, "list_payment_reviewers", reviewers)
    async def exercise():
        await state.update_data(offer_code="silver", offer={"label": "Silver"})
        await payments._submit_payment_from_state(callback.message, state, receipt_message=callback.message)
        assert await state.get_data() == {}
    asyncio.run(exercise())
    assert "رسید شما ثبت شد" in Message.answer.await_args.args[0]
    if delivery == "already_submitted":
        reviewers.assert_not_awaited()
    routes.assert_not_awaited()


@pytest.mark.parametrize("kind", ["photo", "document", None])
@pytest.mark.parametrize("can_review", [True, False])
def test_finance_panel_recovers_receipt_in_private_chat(harness, monkeypatch, kind, can_review):
    callback, _, bot, routes = harness
    callback = callback.model_copy(update={"data": "admin:pay:view:47:pending:1"}).as_(bot)
    monkeypatch.setattr(admin_finance, "get_admin_context", AsyncMock(return_value={
        "is_admin": True, "permissions": ["payments.view"] + (["payments.review"] if can_review else []),
    }))
    monkeypatch.setattr(admin_finance, "get_admin_payment", AsyncMock(return_value=payment_record(kind=kind)))
    asyncio.run(admin_finance.show_payment_receipt(callback))
    details = bot.send_message.await_args.kwargs
    assert details["chat_id"] == callback.from_user.id
    assert ("payment_admin:approve:47" in str(details["reply_markup"])) is can_review
    assert "Customer &lt;name&gt;" in details["text"]
    if kind:
        getattr(bot, f"send_{kind}").assert_awaited_once()
    routes.assert_not_awaited()


def test_missing_attachment_preserves_review_details_and_recovery_link(harness):
    _, _, bot, _ = harness
    bot.send_photo.side_effect = RuntimeError("file temporarily unavailable")
    asyncio.run(payment_delivery.send_private_receipt(
        bot, chat_id=111, payment=payment_record(), text="Payment 47", reply_markup=None,
    ))
    assert "فهرست پرداخت‌ها" in bot.send_message.await_args.kwargs["text"]
    with pytest.raises(ValueError):
        asyncio.run(payment_delivery.send_private_receipt(
            bot, chat_id=-100900, payment=payment_record(), text="Payment 47", reply_markup=None,
        ))


async def confirm(callback, state):
    await state.set_state(AdminPaymentStates.confirming_approval)
    await state.update_data(approval_payment_id=47,
                            approval_expires_at=datetime.now(timezone.utc).timestamp() + 300)
    await payments.approve_payment_callback(callback, state)


def test_only_first_final_approval_sends_text_report_even_if_status_edit_fails(harness, monkeypatch):
    callback, state, bot, _ = harness
    approve = AsyncMock(side_effect=[action_result("approved"), action_result("approved", already_reviewed=True)])
    monkeypatch.setattr(payments, "approve_manual_payment", approve)
    monkeypatch.setattr(payments, "_edit_receipt_status", AsyncMock(side_effect=RuntimeError("message deleted")))
    async def exercise():
        await confirm(callback, state)
        await confirm(callback, state)
    asyncio.run(exercise())
    bot.send_message.assert_awaited_once()
    report = bot.send_message.await_args.kwargs
    assert report["chat_id"] == -100900 and report["message_thread_id"] == 91
    assert "گزارش تأیید پرداخت" in report["text"]
    assert "Customer &lt;name&gt;" in report["text"]
    assert "79,000" in report["text"]
    assert "reply_markup" not in report
    bot.send_photo.assert_not_awaited()
    bot.send_document.assert_not_awaited()
    payments._notify_user_approved.assert_awaited_once()


def test_topic_failure_keeps_approval_and_offers_report_only_retry(harness, monkeypatch):
    callback, state, bot, _ = harness
    approve = AsyncMock(return_value=action_result("approved"))
    monkeypatch.setattr(payments, "approve_manual_payment", approve)
    bot.send_message.side_effect = RuntimeError("Topic unavailable")
    asyncio.run(confirm(callback, state))
    assert "payment_admin:report:47" in str(Message.answer.await_args.kwargs["reply_markup"])
    payments._notify_user_approved.assert_awaited_once()
    bot.send_message.side_effect = None
    monkeypatch.setattr(payments, "get_admin_payment", AsyncMock(return_value=payment_record("approved")))
    retry = callback.model_copy(update={"data": "payment_admin:report:47"}).as_(bot)
    asyncio.run(payments.retry_payment_report(retry))
    approve.assert_awaited_once()
    assert Message.edit_text.await_args.kwargs["reply_markup"] is None


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_report_retry_cannot_report_unapproved_payment(harness, monkeypatch, status):
    callback, _, bot, routes = harness
    monkeypatch.setattr(payments, "get_admin_payment", AsyncMock(return_value=payment_record(status)))
    asyncio.run(payments.retry_payment_report(callback))
    bot.send_message.assert_not_awaited()
    routes.assert_not_awaited()


def test_disabled_topics_do_not_affect_private_approval(harness, monkeypatch):
    callback, state, bot, routes = harness
    routes.return_value = {"enabled": False}
    monkeypatch.setattr(payments, "approve_manual_payment", AsyncMock(return_value=action_result("approved")))
    asyncio.run(confirm(callback, state))
    payments._notify_user_approved.assert_awaited_once()
    bot.send_message.assert_not_awaited()
    Message.answer.assert_not_awaited()


def test_rejected_payment_notifies_customer_without_a_topic_report(harness, monkeypatch):
    callback, state, bot, routes = harness
    callback = callback.model_copy(update={"data": "payment_admin:reject-confirm:47"}).as_(bot)
    result = action_result("rejected")
    result["payment"]["rejection_reason"] = "Not matched"
    monkeypatch.setattr(payments, "reject_manual_payment", AsyncMock(return_value=result))
    monkeypatch.setattr(payments, "_edit_receipt_status_by_id", AsyncMock(side_effect=RuntimeError("message deleted")))
    async def exercise():
        await state.update_data(payment_id=47, rejection_reason="Not matched",
                                receipt_chat_id=12345, receipt_message_id=10)
        await payments.confirm_payment_rejection(callback, state)
    asyncio.run(exercise())
    payments._notify_user_rejected.assert_awaited_once()
    routes.assert_not_awaited()
    bot.send_message.assert_not_awaited()


def test_legacy_topic_review_and_unauthorized_private_review_are_blocked(harness, monkeypatch):
    callback, state, bot, routes = harness
    group_message = callback.message.model_copy(update={"chat": Chat(id=-100900, type="supergroup")}).as_(bot)
    group_callback = callback.model_copy(update={"message": group_message}).as_(bot)
    approve = AsyncMock()
    monkeypatch.setattr(payments, "approve_manual_payment", approve)
    asyncio.run(confirm(group_callback, state))
    payments.get_admin_context.assert_not_awaited()
    monkeypatch.setattr(payments, "get_admin_context", AsyncMock(return_value={"is_admin": False, "permissions": []}))
    asyncio.run(confirm(callback, state))
    approve.assert_not_awaited()
    bot.send_message.assert_not_awaited()
    routes.assert_not_awaited()


def test_panel_status_text_is_replaced_on_review():
    original = "🧾 جزئیات پرداخت\nوضعیت: <b>در انتظار بررسی ⏳</b>\nکاربر: Test"
    updated = payments._status_caption(original, "✅ تأیید شد")
    assert "در انتظار" not in updated and "کاربر: Test" in updated


def test_usdt_report_preserves_decimal_amount_and_english_labels():
    payment = {**payment_record("approved", None), "payment_method": "usdt", "amount": "3.1250"}
    token = ui_language.set("en")
    try:
        report = payments._approval_report(payment)
    finally:
        ui_language.reset(token)
    assert "3.125 USDT" in report and "Payment approval report" in report
    assert "2026-09-28" in report
    assert "تومان" not in report
