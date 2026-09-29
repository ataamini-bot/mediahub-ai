"""Credit changes and subscription purchases share the customer's row lock."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select, text, update

from app.models.credit import CreditAccount, CreditEntry, CreditNotice
from app.models.payment import Payment, PaymentStatus
from app.models.user import User, UserStatus
from app.schemas.credit import MAX_BALANCE
from app.services.admin_access import AdminAccessService
from app.services.audit import AuditService
from app.services.customers import digest
from app.services.managed_settings import ensure_public_operation
from app.services.payment import PaymentService
from app.services.payment_orders import PaymentOrderError, PaymentOrderService


class CreditError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def now_utc():
    return datetime.now(timezone.utc)


class CreditService:
    def __init__(self, db):
        self.db = db

    async def user(self, telegram_id, *, lock=False):
        query = select(User).where(User.telegram_id == telegram_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        user = await self.db.scalar(query)
        if user is None:
            raise LookupError("credit_user_not_found")
        return user

    async def account(self, user_id, currency, *, create=False):
        account = await self.db.scalar(select(CreditAccount).where(
            CreditAccount.user_id == user_id, CreditAccount.currency == currency)
            .execution_options(populate_existing=True))
        if account is None and create:
            # All writers first lock the User row, including checkout and reversals.
            account = CreditAccount(user_id=user_id, currency=currency)
            self.db.add(account)
            await self.db.flush()
        return account

    async def profile(self, telegram_id, currency, *, page=1, admin=False):
        user = await self.user(telegram_id)
        account = await self.account(user.id, currency)
        query = select(CreditEntry).where(CreditEntry.account_id == (account.id if account else -1))
        entries = (await self.db.scalars(query.order_by(CreditEntry.created_at.desc(), CreditEntry.id.desc())
            .offset((page-1)*8).limit(8))).all()
        ids = [row.id for row in entries]
        reversals = set((await self.db.scalars(select(CreditEntry.reverses_entry_id)
            .where(CreditEntry.reverses_entry_id.in_(ids)))).all()) if ids else set()
        notices = {}
        if admin and ids:
            for notice in (await self.db.scalars(select(CreditNotice).where(CreditNotice.entry_id.in_(ids)))).all():
                notices.setdefault(str(notice.entry_id), {})[notice.kind] = notice.status
        return {"telegram_id": user.telegram_id, "name": " ".join(filter(None, (user.first_name, user.last_name))),
            "language": user.preferred_language or "fa", "currency": currency,
            "balance": str(account.balance if account else Decimal(0)), "version": account.version if account else 0,
            "total": await self.db.scalar(select(func.count()).select_from(query.subquery())), "page": page,
            "items": [{"id": str(row.id), "kind": row.kind, "delta": str(row.delta),
                "balance_after": str(row.balance_after), "created_at": row.created_at.isoformat(),
                "payment_id": row.payment_id, "reversed": row.id in reversals,
                **({"actor": row.actor_telegram_id, "reason": row.reason,
                    "notices": notices.get(str(row.id), {})} if admin else {})} for row in entries]}

    async def append(self, account, *, entry_id, delta, kind, reason, actor, payload_hash,
                     payment_id=None, reverses=None):
        balance = account.balance + delta
        if balance < 0:
            raise CreditError("credit_insufficient")
        if balance > MAX_BALANCE:
            raise CreditError("credit_limit")
        account.balance = balance
        account.version += 1
        entry = CreditEntry(id=entry_id, account_id=account.id, delta=delta, balance_after=balance,
            kind=kind, reason=reason, actor_telegram_id=actor, payload_hash=payload_hash,
            payment_id=payment_id, reverses_entry_id=reverses)
        self.db.add(entry)
        await self.db.flush()
        self.db.add(CreditNotice(entry_id=entry.id, kind="user"))
        if kind == "purchase":
            self.db.add(CreditNotice(entry_id=entry.id, kind="report"))
        AuditService(self.db).record(action=f"credit.{kind}", actor_telegram_id=actor,
            target_type="credit_entry", target_id=entry.id,
            details={"currency": account.currency, "delta": str(delta), "balance_after": str(balance),
                     "reason": reason, "reverses": str(reverses) if reverses else None, "payment_id": payment_id})
        return entry

    async def change(self, telegram_id, data):
        await AdminAccessService(self.db).require_permission(data.actor_telegram_id, "balances.manage")
        user = await self.user(telegram_id, lock=True)
        payload_hash = digest({"target": telegram_id, **data.model_dump(mode="json", exclude={"request_id"})})
        previous = await self.db.get(CreditEntry, data.request_id)
        if previous:
            if previous.payload_hash != payload_hash:
                raise CreditError("credit_request_reused")
            return {"entry_id": str(previous.id), "replayed": True,
                    "account": await self.profile(telegram_id, data.currency, admin=True)}
        if user.status == UserStatus.DELETED:
            raise CreditError("credit_user_deleted")
        account = await self.account(user.id, data.currency, create=True)
        if account.version != data.expected_version:
            raise CreditError("credit_stale_balance")
        reverses, kind = None, "adjustment"
        if data.action == "reverse":
            original = await self.db.get(CreditEntry, data.entry_id)
            if original is None or original.account_id != account.id or original.kind != "adjustment":
                raise CreditError("credit_invalid_reversal")
            if await self.db.scalar(select(CreditEntry.id).where(CreditEntry.reverses_entry_id == original.id)):
                raise CreditError("credit_already_reversed")
            delta, reverses, kind = -original.delta, original.id, "reversal"
        else:
            delta = data.amount if data.action == "credit" else -data.amount
        entry = await self.append(account, entry_id=data.request_id, delta=delta, kind=kind,
            reason=data.reason, actor=data.actor_telegram_id, payload_hash=payload_hash, reverses=reverses)
        await self.db.commit()
        return {"entry_id": str(entry.id), "replayed": False,
                "account": await self.profile(telegram_id, data.currency, admin=True)}

    async def purchase(self, order_id, telegram_id):
        orders = PaymentOrderService(self.db)
        user = await orders.user(telegram_id, lock=True)
        order = await orders.get(order_id, user.id, lock=True)
        orders.require_currency(user, order.currency)
        if order.destination_snapshot.get("type") != "credit":
            raise CreditError("credit_order_required")
        if order.status == "submitted":
            payment = await self.db.scalar(select(Payment).where(Payment.order_id == order.id))
            if payment is None or payment.status != PaymentStatus.APPROVED or not payment.payment_method.startswith("credit_"):
                raise CreditError("credit_order_closed")
            return {"replayed": True, "payment_id": payment.id, "order_id": str(order.id),
                    "account": await self.profile(telegram_id, order.currency)}
        if order.status != "open":
            raise CreditError("credit_order_closed")
        if await self.db.scalar(select(Payment.id).where(Payment.user_id == user.id, Payment.status == PaymentStatus.PENDING).limit(1)):
            raise PaymentOrderError("pending_payment_exists")
        await ensure_public_operation(self.db, "payments")
        account = await self.account(user.id, order.currency, create=True)
        snapshot = order.offer_snapshot
        amount = Decimal(snapshot["price"])
        if account.balance < amount:
            raise CreditError("credit_insufficient")
        payment = Payment(order_id=order.id, user_id=user.id, plan_id=order.plan_id, amount=amount,
            offer_code=snapshot["code"], duration_days=snapshot["duration_days"],
            plan_name_snapshot=snapshot["label"], plan_limits_snapshot={key: snapshot[key] for key in (
                "daily_download_limit", "max_file_size_mb", "max_quality", "max_concurrent_downloads",
                "priority_processing", "forced_join_required", "download_limit_period")},
            status=PaymentStatus.PENDING, payment_method="credit_usdt" if order.currency == "USDT" else "credit_irt",
            payment_destination_snapshot={"type": "credit", "currency": order.currency})
        self.db.add(payment)
        await self.db.flush()
        # The same transaction commits the debit, payment, subscription and notifications.
        await PaymentService(self.db)._activate(payment, user, admin_telegram_id=None, commit=False)
        await self.append(account, entry_id=order.id, delta=-amount, kind="purchase", reason="Subscription purchase",
            actor=user.telegram_id, payload_hash=digest({"order": str(order.id)}), payment_id=payment.id)
        order.status = "submitted"
        await self.db.commit()
        return {"replayed": False, "payment_id": payment.id, "order_id": str(order.id),
                "account": await self.profile(telegram_id, order.currency)}

    async def claim_notice(self):
        if not await self.db.scalar(text("SELECT pg_try_advisory_xact_lock(6148327412)")):
            return None
        now = now_utc()
        await self.db.execute(update(CreditNotice).where(CreditNotice.status == "sending", CreditNotice.lease_until < now)
                             .values(status="uncertain"))
        if await self.db.scalar(select(CreditNotice.id).where(CreditNotice.status == "sending").limit(1)):
            return None
        cooldown = await self.db.scalar(select(func.max(CreditNotice.available_at)))
        if cooldown and cooldown > now:
            return None
        notice = await self.db.scalar(select(CreditNotice).where(CreditNotice.status == "pending")
            .order_by(CreditNotice.id).limit(1).with_for_update())
        if notice is None or notice.available_at > now:
            return None
        entry = await self.db.get(CreditEntry, notice.entry_id)
        account = await self.db.get(CreditAccount, entry.account_id)
        user = await self.db.get(User, account.user_id)
        notice.status, notice.claim_token = "sending", uuid4()
        notice.lease_until = now + timedelta(seconds=120)
        notice.attempts += 1
        payment = await self.db.get(Payment, entry.payment_id) if entry.payment_id else None
        return {"id": notice.id, "claim_token": str(notice.claim_token), "kind": notice.kind,
            "telegram_id": user.telegram_id, "language": user.preferred_language or "fa",
            "currency": account.currency, "delta": str(entry.delta), "balance_after": str(entry.balance_after),
            "entry_id": str(entry.id), "entry_kind": entry.kind,
            "payment_id": entry.payment_id, "plan_name": payment.plan_name_snapshot if payment else None,
            "duration_days": payment.duration_days if payment else None}

    async def ack_notice(self, notice_id, data):
        notice = await self.db.scalar(select(CreditNotice).where(CreditNotice.id == notice_id).with_for_update()
                                     .execution_options(populate_existing=True))
        if notice is None:
            raise LookupError("credit_notice_not_found")
        if notice.claim_token != data.claim_token:
            raise CreditError("credit_notice_claim_changed")
        if notice.status not in {"sending", "uncertain"} or (notice.status == "uncertain" and data.status != "sent"):
            return {"status": notice.status}
        notice.status = data.status
        if data.status == "retry":
            notice.status = "pending" if notice.attempts < 5 else "failed"
            notice.available_at = now_utc() + timedelta(seconds=data.retry_after)
        return {"status": notice.status}


async def reconcile_credit_notices_after_restore(db):
    # A restored snapshot cannot tell which notifications were sent after its creation.
    result = await db.execute(update(CreditNotice).where(CreditNotice.status.in_(["pending", "sending"]))
                              .values(status="uncertain"))
    return result.rowcount
