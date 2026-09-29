"""Durable, serialized broadcasts. Ambiguous sends are never auto-replayed."""
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import exists, func, literal, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.models.broadcast import Broadcast, BroadcastRecipient
from app.models.payment import Payment, PaymentStatus
from app.models.plan import Plan
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User, UserStatus
from app.services.admin_access import AdminAccessService
from app.services.audit import AuditService

QUEUE_LOCK = 6_148_327_411
PERMISSION = "broadcasts.manage"


class BroadcastConflict(ValueError):
    pass


def now_utc():
    return datetime.now(timezone.utc)


def audience(payload, now):
    active = select(Subscription.id).join(Plan, Plan.id == Subscription.plan_id).where(
        Subscription.user_id == User.id,
        Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.SCHEDULED]),
        Subscription.started_at <= now, Subscription.expires_at > now, Plan.slug != "free",
    )
    paid = exists(select(Payment.id).where(Payment.user_id == User.id, Payment.status == PaymentStatus.APPROVED))
    statement = select(User).where(User.status == UserStatus.ACTIVE, User.telegram_id > 0)
    segment = payload["segment"]
    if segment == "active":
        statement = statement.where(exists(active))
    elif segment == "plan":
        statement = statement.where(exists(active.where(Subscription.plan_id == payload["plan_id"])))
    elif segment == "never_paid":
        statement = statement.where(~paid, ~exists(active))
    elif segment == "former":
        statement = statement.where(paid, ~exists(active))
    elif segment == "manual":
        statement = statement.where(User.telegram_id.in_(payload["telegram_ids"]))
    if payload["language"] != "all":
        statement = statement.where(func.coalesce(User.preferred_language, "fa") == payload["language"])
    return statement


async def cancel_restored_broadcasts(db):
    """Run with senders stopped: an older snapshot cannot prove post-backup deliveries."""
    jobs = await db.execute(update(Broadcast).where(
        Broadcast.status.in_(["draft", "queued", "running", "paused"])
    ).values(status="cancelled", finished_at=now_utc(), last_error="database_restored"))
    pending = await db.execute(update(BroadcastRecipient).where(
        BroadcastRecipient.status == "pending").values(status="cancelled"))
    uncertain = await db.execute(update(BroadcastRecipient).where(
        BroadcastRecipient.status == "sending").values(status="uncertain", error_code="transport_uncertain"))
    return {"broadcasts_cancelled": jobs.rowcount, "broadcast_deliveries_cancelled": pending.rowcount,
            "broadcast_deliveries_uncertain": uncertain.rowcount}


class BroadcastService:
    def __init__(self, db):
        self.db = db

    async def load(self, broadcast_id, *, lock=False):
        statement = select(Broadcast).where(Broadcast.id == broadcast_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        job = await self.db.scalar(statement)
        if job is None:
            raise LookupError("Broadcast not found")
        return job

    async def detail(self, job):
        counts = dict((await self.db.execute(select(BroadcastRecipient.status, func.count())
            .where(BroadcastRecipient.broadcast_id == job.id).group_by(BroadcastRecipient.status))).all())
        languages = dict((await self.db.execute(select(BroadcastRecipient.language, func.count())
            .where(BroadcastRecipient.broadcast_id == job.id).group_by(BroadcastRecipient.language))).all())
        return {"id": job.id, "status": job.status, "payload": job.payload, "counts": counts,
            "total": sum(counts.values()), "languages": languages, "actor_telegram_id": job.actor_telegram_id,
            "confirmation_token": job.confirmation_token, "expires_at": job.expires_at.isoformat(),
            "created_at": job.created_at.isoformat(), "last_error": job.last_error}

    async def create(self, data):
        payload = data.model_dump(mode="json", exclude={"actor_telegram_id", "request_id"})
        if data.plan_id:
            plan = await self.db.scalar(select(Plan).where(Plan.id == data.plan_id, Plan.is_active.is_(True),
                                                        Plan.deleted_at.is_(None), Plan.slug != "free"))
            if plan is None:
                raise BroadcastConflict("Choose an active custom plan")
        job_id = await self.db.scalar(insert(Broadcast).values(
            request_id=str(data.request_id), actor_telegram_id=data.actor_telegram_id, payload=payload,
            confirmation_token=str(uuid.uuid4()), expires_at=now_utc() + timedelta(minutes=5),
        ).on_conflict_do_nothing(index_elements=["request_id"]).returning(Broadcast.id))
        if job_id is None:
            job = await self.db.scalar(select(Broadcast).where(Broadcast.request_id == str(data.request_id)))
            if job.actor_telegram_id != data.actor_telegram_id or job.payload != payload:
                raise BroadcastConflict("Request ID already used for a different draft")
            return job
        users = audience(payload, now_utc()).order_by(User.id).with_only_columns(
            literal(job_id), User.id, User.telegram_id, func.coalesce(User.preferred_language, "fa"),
        )
        await self.db.execute(insert(BroadcastRecipient).from_select(
            ["broadcast_id", "user_id", "telegram_id", "language"], users))
        AuditService(self.db).record(action="broadcast.drafted", actor_telegram_id=data.actor_telegram_id,
                                     target_type="broadcast", target_id=job_id)
        return await self.load(job_id)

    async def confirm(self, broadcast_id, data):
        job = await self.load(broadcast_id, lock=True)
        if job.actor_telegram_id != data.actor_telegram_id or job.confirmation_token != str(data.confirmation_token):
            raise BroadcastConflict("Confirmation belongs to another draft or administrator")
        if job.status != "draft":
            return job  # Replayed confirmation cannot queue a second campaign.
        if job.expires_at < now_utc():
            raise BroadcastConflict("Preview expired; create a new draft")
        if not await self.db.scalar(select(BroadcastRecipient.id).where(BroadcastRecipient.broadcast_id == job.id).limit(1)):
            raise BroadcastConflict("No eligible recipients")
        job.status = "queued"
        job.started_at = now_utc()
        AuditService(self.db).record(action="broadcast.confirmed", actor_telegram_id=data.actor_telegram_id,
                                     target_type="broadcast", target_id=job.id)
        return job

    async def control(self, broadcast_id, actor_id, action):
        job = await self.load(broadcast_id, lock=True)
        if action == "pause" and job.status in {"queued", "running"}:
            job.status = "paused"
        elif action == "resume" and job.status == "paused":
            job.status = "queued"
            job.last_error = None
        elif action == "cancel" and job.status in {"draft", "queued", "running", "paused"}:
            job.status = "cancelled"
            job.finished_at = now_utc()
            await self.db.execute(update(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == job.id,
                BroadcastRecipient.status == "pending").values(status="cancelled"))
        else:
            raise BroadcastConflict("This action is not available for the current status")
        AuditService(self.db).record(action=f"broadcast.{action}", actor_telegram_id=actor_id,
                                     target_type="broadcast", target_id=job.id)
        return job

    async def claim(self):
        # One in-flight request for the entire bot token, even with two senders.
        if not await self.db.scalar(text(f"SELECT pg_try_advisory_xact_lock({QUEUE_LOCK})")):
            return None
        now = now_utc()
        expired = (await self.db.execute(select(BroadcastRecipient.id, BroadcastRecipient.broadcast_id).where(
            BroadcastRecipient.status == "sending", BroadcastRecipient.lease_until < now))).all()
        for recipient_id, job_id in expired:
            job = await self.load(job_id, lock=True)
            item = await self.db.scalar(select(BroadcastRecipient).where(BroadcastRecipient.id == recipient_id)
                .with_for_update().execution_options(populate_existing=True))
            # An acknowledgement may have committed while we waited for the campaign lock.
            if item.status != "sending" or item.lease_until >= now:
                continue
            item.status = "uncertain"
            item.error_code = "transport_uncertain"
            if job.status in {"queued", "running"}:
                job.status = "paused"
                job.last_error = "transport_uncertain"
        await self.db.flush()
        if await self.db.scalar(select(BroadcastRecipient.id).where(BroadcastRecipient.status == "sending").limit(1)):
            return None
        # Telegram flood waits apply to the token, even if that campaign is cancelled.
        cooldown = await self.db.scalar(select(func.max(Broadcast.next_send_at)))
        if cooldown and cooldown > now:
            return None
        # Bound the amount of stale audience cleanup in any one request.
        for _ in range(20):
            job = await self.db.scalar(select(Broadcast).where(Broadcast.status.in_(["queued", "running"]))
                                      .order_by(Broadcast.id).limit(1).with_for_update()
                                      .execution_options(populate_existing=True))
            if job is None:
                return None
            context = await AdminAccessService(self.db).get_context(job.actor_telegram_id)
            if not context.is_admin or not context.has_permission(PERMISSION):
                job.status, job.last_error = "paused", "permission_revoked"
                await self.db.flush()
                continue
            if job.next_send_at and job.next_send_at > now:
                return None
            item = await self.db.scalar(select(BroadcastRecipient).where(
                BroadcastRecipient.broadcast_id == job.id, BroadcastRecipient.status == "pending",
                BroadcastRecipient.available_at <= now,
            ).order_by(BroadcastRecipient.id).limit(1).with_for_update())
            if item is None:
                pending = await self.db.scalar(select(BroadcastRecipient.id).where(
                    BroadcastRecipient.broadcast_id == job.id, BroadcastRecipient.status == "pending").limit(1))
                if pending is not None:
                    return None
                job.status, job.finished_at = "completed", now
                await self.db.flush()
                continue
            user = await self.db.scalar(audience(job.payload, now).where(User.id == item.user_id))
            if user is None:
                item.status, item.error_code = "skipped", "audience_changed"
                await self.db.flush()
                continue
            language = user.preferred_language or "fa"
            item.language = language
            item.status, item.claim_token = "sending", str(uuid.uuid4())
            item.lease_until = now + timedelta(seconds=120)
            item.attempts += 1
            job.status = "running"
            return {"recipient_id": item.id, "broadcast_id": job.id, "telegram_id": item.telegram_id,
                    "claim_token": item.claim_token, "content": job.payload["variants"][language],
                    "button": job.payload["buttons"].get(language)}
        return None

    async def acknowledge(self, recipient_id, data):
        # Lock ordering matches claim/control: campaign, then recipient.
        job_id = await self.db.scalar(select(BroadcastRecipient.broadcast_id).where(BroadcastRecipient.id == recipient_id))
        job = await self.load(job_id, lock=True)
        item = await self.db.scalar(select(BroadcastRecipient).where(BroadcastRecipient.id == recipient_id)
                                    .with_for_update().execution_options(populate_existing=True))
        if item.claim_token != str(data.claim_token):
            raise BroadcastConflict("Delivery lease changed")
        if item.status not in {"sending", "uncertain"}:
            return {"status": item.status}
        # A late success can resolve uncertainty; never put an uncertain send back into the queue.
        if item.status == "uncertain" and data.outcome != "sent":
            return {"status": item.status}
        item.status, item.error_code = data.outcome, data.error_code
        item.telegram_message_id = data.message_id
        if data.outcome == "retry":
            item.status = "pending" if item.attempts < 5 and job.status != "cancelled" else "failed"
            item.available_at = now_utc() + timedelta(seconds=max(1, data.retry_after))
            job.next_send_at = item.available_at
        else:
            job.next_send_at = now_utc() + timedelta(seconds=0.15)
        if job.status in {"running", "queued"} and (data.outcome == "uncertain" or data.error_code == "invalid_content"):
            job.status = "paused"
            job.last_error = data.error_code
        return {"status": item.status}
