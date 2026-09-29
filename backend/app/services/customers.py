"""Support operations serialized with checkout approval on the customer row."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from app.models.admin import AdminAccount
from app.models.customer_action import CustomerAction
from app.models.credit import CreditAccount
from app.models.download_job import DownloadJob, DownloadJobStatus
from app.models.payment import Payment, PaymentStatus
from app.models.plan import Plan
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User, UserStatus
from app.services.admin_access import AdminAccessService
from app.services.audit import AuditService
from app.services.download_access import DownloadAccessService, quota_window_start_utc
from app.services.managed_settings import get_managed_setting

PERMISSIONS = {"block": "users.manage", "unblock": "users.manage", "note": "users.manage",
               "reset_quota": "users.manage", "grant": "subscriptions.manage",
               "extend": "subscriptions.manage", "cancel": "subscriptions.manage",
               "change_plan": "subscriptions.manage"}


class CustomerConflict(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def subscription_data(row, plan):
    return {"id": row.id, "plan_id": row.plan_id, "plan_name": plan.name,
            "plan_name_en": plan.name_en or f"Plan {plan.id}",
            "status": row.status.value, "started_at": row.started_at.isoformat(),
            "expires_at": row.expires_at.isoformat(), "limit": row.daily_download_limit,
            "period": row.download_limit_period}


class CustomerService:
    def __init__(self, db):
        self.db = db

    async def search(self, query, page=1):
        query = query.strip().lstrip("@")
        statement = select(User)
        if query.isdecimal():
            if int(query) > 2**63 - 1:
                return {"total": 0, "page": page, "items": []}
            statement = statement.where(User.telegram_id == int(query))
        else:
            statement = statement.where(or_(User.username.icontains(query, autoescape=True),
                User.first_name.icontains(query, autoescape=True), User.last_name.icontains(query, autoescape=True)))
        total = await self.db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (await self.db.scalars(statement.order_by(User.id.desc()).offset((page - 1) * 10).limit(10))).all()
        return {"total": total, "page": page, "items": [{"telegram_id": u.telegram_id,
                "name": " ".join(filter(None, (u.first_name, u.last_name))),
                "username": u.username, "status": u.status.value} for u in rows]}

    async def user(self, telegram_id, lock=False):
        statement = select(User).where(User.telegram_id == telegram_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        user = await self.db.scalar(statement)
        if user is None:
            raise LookupError("Customer not found")
        return user

    async def subscriptions(self, user):
        return (await self.db.execute(select(Subscription, Plan).join(Plan, Plan.id == Subscription.plan_id)
                .where(Subscription.user_id == user.id).order_by(Subscription.started_at, Subscription.id))).all()

    async def snapshot(self, user):
        return {"status": user.status.value, "quota_reset": user.quota_reset_at.isoformat() if user.quota_reset_at else None,
            "conversion_reset": user.conversion_quota_reset_at.isoformat() if user.conversion_quota_reset_at else None,
            "subscriptions": [subscription_data(s, p) for s, p in await self.subscriptions(user)]}

    async def revision(self, user):
        return digest(await self.snapshot(user))

    async def profile(self, telegram_id):
        user = await self.user(telegram_id)
        rows = await self.subscriptions(user)
        now = datetime.now(timezone.utc)
        history = (await self.db.scalars(select(CustomerAction).where(CustomerAction.user_id == user.id)
                   .order_by(CustomerAction.created_at.desc()).limit(10))).all()
        entitlement = await DownloadAccessService(self.db)._resolve_entitlement(user)
        tz = await get_managed_setting(self.db, "quota.timezone")
        window = quota_window_start_utc(entitlement.download_limit_period, timezone_name=tz)
        if user.quota_reset_at:
            window = max(window, user.quota_reset_at)
        usage_filters = [DownloadJob.user_id == user.id, DownloadJob.delivered_at >= window]
        if entitlement.plan_slug == "free":
            usage_filters.append(or_(DownloadJob.media_type.is_(None), DownloadJob.media_type != "convert"))
        used = await self.db.scalar(select(func.count()).select_from(DownloadJob).where(*usage_filters))
        pending = await self.db.scalar(select(func.count()).select_from(Payment).where(Payment.user_id == user.id, Payment.status == PaymentStatus.PENDING))
        credits = (await self.db.scalars(select(CreditAccount).where(CreditAccount.user_id == user.id))).all()
        return {"telegram_id": user.telegram_id, "name": " ".join(filter(None, (user.first_name, user.last_name))),
            "username": user.username, "status": user.status.value,
            "language": user.preferred_language or "fa", "revision": await self.revision(user),
            "plan_name": entitlement.plan_name, "used": used, "limit": entitlement.daily_download_limit,
            "period": entitlement.download_limit_period, "pending_payments": pending,
            "credits": {row.currency: str(row.balance) for row in credits},
            "subscriptions": [subscription_data(s, p) for s, p in rows
                              if s.expires_at > now and s.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.SCHEDULED)],
            "history": [{"action": a.action, "reason": a.reason, "actor": a.actor_telegram_id,
                         "at": a.created_at.isoformat()} for a in history]}

    async def change(self, telegram_id, data):
        await AdminAccessService(self.db).require_permission(data.actor_telegram_id, "users.view")
        context = await AdminAccessService(self.db).require_permission(data.actor_telegram_id, PERMISSIONS[data.action])
        user = await self.user(telegram_id, lock=True)
        payload_hash = digest({"target": telegram_id, **data.model_dump(mode="json", exclude={"request_id"})})
        previous = await self.db.get(CustomerAction, str(data.request_id))
        if previous:
            if previous.payload_hash != payload_hash or previous.user_id != user.id:
                raise CustomerConflict("Request id was already used for another action")
            return {"replayed": True, "customer": await self.profile(telegram_id)}
        before = await self.revision(user)
        before_state = await self.snapshot(user)
        if before != data.expected_revision:
            raise CustomerConflict("Customer changed. Open the profile and review again.")
        if user.status == UserStatus.DELETED:
            raise CustomerConflict("Deleted accounts cannot be changed")
        protected = await self.db.scalar(select(AdminAccount.id).where(AdminAccount.user_id == user.id, AdminAccount.is_active.is_(True)))
        if protected and data.action != "note":
            raise CustomerConflict("Use administrator management for admin accounts")
        now = datetime.now(timezone.utc)
        active = [(s, p) for s, p in await self.subscriptions(user)
                  if s.expires_at > now and s.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.SCHEDULED)]
        selected = next((s for s, _ in active if s.id == data.subscription_id), None)
        if data.action in {"extend", "cancel", "change_plan"} and selected is None:
            raise CustomerConflict("The selected subscription is no longer available")
        plan = None
        if data.action in {"grant", "change_plan"}:
            plan = await self.db.scalar(select(Plan).where(Plan.id == data.plan_id, Plan.is_active.is_(True), Plan.deleted_at.is_(None), Plan.slug != "free"))
            if plan is None:
                raise CustomerConflict("Select an active paid plan")
        if data.action in {"block", "unblock"}:
            user.status = UserStatus.BLOCKED if data.action == "block" else UserStatus.ACTIVE
        elif data.action == "reset_quota":
            user.quota_reset_at = user.conversion_quota_reset_at = now
        elif data.action == "grant":
            start = max([now] + [s.expires_at for s, _ in active])
            self.db.add(Subscription(user_id=user.id, plan_id=plan.id,
                status=SubscriptionStatus.SCHEDULED if start > now else SubscriptionStatus.ACTIVE,
                started_at=start, expires_at=start + timedelta(days=data.days),
                daily_download_limit=plan.daily_download_limit, download_limit_period=plan.download_limit_period))
        elif data.action == "extend":
            end = selected.expires_at
            delta = timedelta(days=data.days)
            selected.expires_at += delta
            for following, _ in active:
                if following.id != selected.id and following.started_at >= end:
                    following.started_at += delta
                    following.expires_at += delta
        elif data.action == "cancel":
            selected.status = SubscriptionStatus.CANCELLED
        elif data.action == "change_plan":
            selected.plan_id = plan.id
            selected.daily_download_limit = plan.daily_download_limit
            selected.download_limit_period = plan.download_limit_period
        self.db.add(CustomerAction(request_id=str(data.request_id), user_id=user.id,
            actor_telegram_id=data.actor_telegram_id, action=data.action, reason=data.reason, payload_hash=payload_hash))
        await self.db.flush()
        AuditService(self.db).record(action="customer." + data.action, actor_user_id=context.user_id,
            actor_telegram_id=context.telegram_id, target_type="user", target_id=user.id,
            details={"reason": data.reason, "request_id": str(data.request_id), "before_revision": before,
                     "after_revision": await self.revision(user), "plan_id": data.plan_id,
                     "subscription_id": data.subscription_id, "days": data.days,
                     "before": before_state, "after": await self.snapshot(user)})
        await self.db.commit()
        return {"replayed": False, "customer": await self.profile(telegram_id)}
