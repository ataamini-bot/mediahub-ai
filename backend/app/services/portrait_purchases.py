"""Single-photo checkout reuses audited payments without activating a subscription."""
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import func, select
from app.models.portrait_credit import PortraitCredit
from app.models.payment import PaymentStatus
from app.models.user import User, UserStatus
from app.services.managed_settings import get_managed_setting


async def portrait_offer(db, *, currency):
    from app.services.media_tool_config import read_config
    from app.services.payment_offers import PaymentOffer
    config = await read_config(db, "portrait")
    amount = Decimal(await get_managed_setting(db, f"portrait.price_{currency.lower()}"))
    if not config["enabled"] or not config["token"] or amount <= 0:
        raise LookupError("Single portrait purchase is unavailable")
    return PaymentOffer(code="portrait-once", label="One portrait photo" if currency == "USDT" else "ساخت یک عکس پرسنلی",
        plan_id=None, duration_days=0, price=amount, purpose="portrait", daily_download_limit=None,
        max_file_size_mb=None, max_quality=None, max_concurrent_downloads=1, priority_processing=False,
        forced_join_required=True, description="یک نوبت ساخت عکس پرسنلی، بدون خرید یا تغییر اشتراک. بدون تاریخ انقضا.",
        description_en="One portrait photo, without a subscription or changes to your plan. No expiry.")


async def activate_portrait(db, payment, user, *, admin_telegram_id, commit):
    from app.services.payment import PaymentActionResult, PaymentConflict
    from app.services.audit import AuditService
    if payment.status == PaymentStatus.APPROVED:
        return PaymentActionResult(payment=payment, user=user, already_reviewed=True)
    if payment.status == PaymentStatus.REJECTED:
        raise PaymentConflict("Rejected payment cannot be approved")
    if user.status != UserStatus.ACTIVE:
        raise PermissionError("User account is not active")
    db.add(PortraitCredit(payment_id=payment.id, user_id=user.id))
    payment.status = PaymentStatus.APPROVED
    payment.reviewed_at = datetime.now(timezone.utc)
    payment.reviewed_by_telegram_id = admin_telegram_id
    payment.rejection_reason = None
    payment.subscription_change_type = "portrait"
    AuditService(db).record(action="payment.approved" if admin_telegram_id else "payment.credit_paid",
        actor_telegram_id=admin_telegram_id or user.telegram_id, target_type="payment", target_id=payment.id,
        details={"purpose": "portrait", "units": 1})
    if commit:
        await db.commit()
        await db.refresh(payment)
    else:
        await db.flush()
    return PaymentActionResult(payment=payment, user=user)


async def balance(db, telegram_id):
    return await db.scalar(select(func.count()).select_from(PortraitCredit).join(User, User.id == PortraitCredit.user_id)
        .where(User.telegram_id == telegram_id, User.status == UserStatus.ACTIVE, PortraitCredit.request_id.is_(None)))


def refund_failed_credit(db, row):
    # Failed before a usable result, or cancelled before a provider call. Unknown
    # provider/delivery outcomes need review, never another automatic generation.
    if row.kind != "portrait" or not (row.status == "failed" or (row.status == "cancelled" and not row.started_at)):
        return
    credit = db.scalar(select(PortraitCredit).where(PortraitCredit.request_id == row.id).with_for_update())
    if credit:
        credit.request_id = None
