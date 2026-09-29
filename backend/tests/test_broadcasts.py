"""Exercise the durable queue against PostgreSQL, including concurrent workers."""
import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update

from app.api.broadcasts import router
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.admin import AdminAccount
from app.models.broadcast import Broadcast, BroadcastRecipient
from app.models.payment import Payment, PaymentStatus
from app.models.plan import Plan
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User, UserStatus
from app.schemas.broadcast import BroadcastAck, BroadcastConfirm, BroadcastCreate
from app.services.broadcasts import BroadcastConflict, BroadcastService, audience, cancel_restored_broadcasts, now_utc


@pytest_asyncio.fixture
async def records():
    async with AsyncSessionLocal() as db:
        base = 8_700_000_000_000 + uuid.uuid4().int % 100_000_000
        users = [User(telegram_id=base+i, first_name="Broadcast test", status=UserStatus.ACTIVE,
                      preferred_language="en" if i == 2 else "fa") for i in range(7)]
        users[4].status = UserStatus.BLOCKED
        users[5].preferred_language = None
        users[5].language_code = "en"  # Telegram metadata does not override the bot's language.
        users[6].status = UserStatus.DELETED
        plans = [Plan(name="Broadcast plan", name_en="Broadcast plan", slug="bc_" + uuid.uuid4().hex,
            price=Decimal(100), duration_days=30, daily_download_limit=3, max_file_size_mb=300,
            max_quality=720, max_concurrent_downloads=1, is_system=False, is_active=True) for _ in range(2)]
        db.add_all(users + plans)
        await db.flush()
        db.add(AdminAccount(user_id=users[0].id, is_superadmin=True, is_active=True,
                            created_by_user_id=users[0].id))
        for i in (2, 3):
            db.add(Payment(user_id=users[i].id, plan_id=plans[0].id, amount=100, offer_code="bc",
                           duration_days=30, plan_name_snapshot="Broadcast plan", status=PaymentStatus.APPROVED))
        db.add_all([
            Subscription(user_id=users[2].id, plan_id=plans[0].id, status=SubscriptionStatus.ACTIVE,
                         started_at=now_utc()-timedelta(days=1), expires_at=now_utc()+timedelta(days=5)),
            Subscription(user_id=users[3].id, plan_id=plans[0].id, status=SubscriptionStatus.EXPIRED,
                         started_at=now_utc()-timedelta(days=40), expires_at=now_utc()-timedelta(days=10)),
            Subscription(user_id=users[5].id, plan_id=plans[1].id, status=SubscriptionStatus.SCHEDULED,
                         started_at=now_utc()+timedelta(days=2), expires_at=now_utc()+timedelta(days=32)),
        ])
        await db.commit()
    try:
        yield SimpleNamespace(users=users, admin=users[0], plans=plans)
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Broadcast).where(Broadcast.actor_telegram_id == users[0].telegram_id))
            await db.execute(delete(User).where(User.id.in_([u.id for u in users])))
            await db.execute(delete(Plan).where(Plan.id.in_([p.id for p in plans])))
            await db.commit()
        await engine.dispose()


def request(records, indices=(1, 2), language="all", **changes):
    return BroadcastCreate(actor_telegram_id=records.admin.telegram_id, request_id=uuid.uuid4(),
        **{"segment": "manual", "language": language,
           "telegram_ids": [records.users[i].telegram_id for i in indices],
           "variants": {lang: {"kind": "text", "text": "سلام" if lang == "fa" else "Hello"}
                        for lang in (["fa", "en"] if language == "all" else [language])}, **changes})


async def make_job(records, *, confirmed=True, **kwargs):
    async with AsyncSessionLocal() as db:
        service = BroadcastService(db)
        job = await service.create(request(records, **kwargs))
        if confirmed:
            await service.confirm(job.id, BroadcastConfirm(actor_telegram_id=records.admin.telegram_id,
                                                          confirmation_token=job.confirmation_token))
        await db.commit()
        return job


async def claim():
    async with AsyncSessionLocal() as db:
        result = await BroadcastService(db).claim()
        await db.commit()
        return result


async def ack(delivery, outcome="sent", **kwargs):
    if outcome == "sent":
        kwargs.setdefault("message_id", 100)
    async with AsyncSessionLocal() as db:
        result = await BroadcastService(db).acknowledge(delivery["recipient_id"],
            BroadcastAck(claim_token=delivery["claim_token"], outcome=outcome, **kwargs))
        await db.commit()
        return result


async def clear_cooldown():
    async with AsyncSessionLocal() as db:
        await db.execute(update(Broadcast).values(next_send_at=None))
        await db.execute(update(BroadcastRecipient).values(available_at=now_utc()-timedelta(seconds=1)))
        await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("segment,expected", [
    ("all", {0, 1, 2, 3, 5}), ("never_paid", {0, 1, 5}), ("active", {2}),
    ("former", {3}), ("plan", {2}), ("manual", {1, 2}),
])
async def test_segments_use_current_entitlements_and_exclude_blocked_deleted(records, segment, expected):
    payload = {"segment": segment, "language": "all", "plan_id": records.plans[0].id,
               "telegram_ids": [records.users[i].telegram_id for i in (1, 2, 4, 6)]}
    async with AsyncSessionLocal() as db:
        rows = (await db.scalars(audience(payload, now_utc()).where(User.id.in_([u.id for u in records.users])))).all()
        assert {u.id for u in rows} == {records.users[i].id for i in expected}


@pytest.mark.asyncio
async def test_language_snapshot_and_granted_subscription(records):
    job = await make_job(records, confirmed=False, indices=(1, 2, 4, 5, 6), language="fa")
    async with AsyncSessionLocal() as db:
        detail = await BroadcastService(db).detail(await db.get(Broadcast, job.id))
        assert detail["languages"] == {"fa": 2}
        assert detail["status"] == "draft"
        assert await BroadcastService(db).claim() is None
        # Gifted current paid-plan access is an active subscription, not the free audience.
        db.add(Subscription(user_id=records.users[1].id, plan_id=records.plans[0].id,
            status=SubscriptionStatus.SCHEDULED, started_at=now_utc()-timedelta(days=1),
            expires_at=now_utc()+timedelta(days=2)))
        await db.flush()
        assert await db.scalar(audience({"segment": "active", "language": "fa"}, now_utc())
                               .where(User.id == records.users[1].id))
        assert await db.scalar(audience({"segment": "never_paid", "language": "fa"}, now_utc())
                               .where(User.id == records.users[1].id)) is None


@pytest.mark.asyncio
async def test_concurrent_drafts_and_confirmations_are_idempotent(records):
    data = request(records)
    async def create():
        async with AsyncSessionLocal() as db:
            job = await BroadcastService(db).create(data)
            await db.commit()
            return job
    first, second = await asyncio.wait_for(asyncio.gather(create(), create()), 10)
    assert first.id == second.id
    async def confirm():
        async with AsyncSessionLocal() as db:
            result = await BroadcastService(db).confirm(first.id, BroadcastConfirm(
                actor_telegram_id=records.admin.telegram_id, confirmation_token=first.confirmation_token))
            await db.commit()
            return result.status
    assert await asyncio.gather(confirm(), confirm()) == ["queued", "queued"]
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(BroadcastRecipient)
                               .where(BroadcastRecipient.broadcast_id == first.id)) == 2
        with pytest.raises(BroadcastConflict):
            await BroadcastService(db).create(data.model_copy(update={"language": "fa"}))


@pytest.mark.asyncio
async def test_confirmation_rejects_other_owner_token_expiration_and_empty_audience(records):
    job = await make_job(records, confirmed=False)
    async with AsyncSessionLocal() as db:
        service = BroadcastService(db)
        for actor_id, token in ((records.users[1].telegram_id, job.confirmation_token),
                                (records.admin.telegram_id, str(uuid.uuid4()))):
            with pytest.raises(BroadcastConflict):
                await service.confirm(job.id, BroadcastConfirm(actor_telegram_id=actor_id, confirmation_token=token))
        stored = await db.get(Broadcast, job.id)
        stored.expires_at = now_utc()-timedelta(seconds=1)
        await db.flush()
        with pytest.raises(BroadcastConflict):
            await service.confirm(job.id, BroadcastConfirm(actor_telegram_id=records.admin.telegram_id,
                                                          confirmation_token=job.confirmation_token))
    empty = await make_job(records, confirmed=False, indices=(4, 6))
    async with AsyncSessionLocal() as db:
        with pytest.raises(BroadcastConflict):
            await BroadcastService(db).confirm(empty.id, BroadcastConfirm(
                actor_telegram_id=records.admin.telegram_id, confirmation_token=empty.confirmation_token))


@pytest.mark.asyncio
async def test_two_workers_claim_once_ack_replay_and_new_session_resume(records):
    job = await make_job(records)
    results = await asyncio.wait_for(asyncio.gather(claim(), claim()), 10)
    deliveries = [r for r in results if r]
    assert len(deliveries) == 1
    first = deliveries[0]
    assert await ack(first) == {"status": "sent"}
    assert await ack(first) == {"status": "sent"}
    assert await claim() is None  # Token-wide send pacing.
    await clear_cooldown()
    second = await claim()
    assert second["recipient_id"] != first["recipient_id"]
    assert second["content"]["text"] == "Hello"
    await ack(second, "blocked", error_code="forbidden")
    await clear_cooldown()
    assert await claim() is None
    async with AsyncSessionLocal() as db:
        detail = await BroadcastService(db).detail(await db.get(Broadcast, job.id))
        assert detail["status"] == "completed"
        assert detail["counts"] == {"sent": 1, "blocked": 1}


@pytest.mark.asyncio
async def test_pause_resume_cancel_preserves_inflight_outcome(records):
    job = await make_job(records)
    first = await claim()
    async with AsyncSessionLocal() as db:
        service = BroadcastService(db)
        await service.control(job.id, records.admin.telegram_id, "pause")
        await db.commit()
        assert await service.claim() is None
        await service.control(job.id, records.admin.telegram_id, "resume")
        await service.control(job.id, records.admin.telegram_id, "cancel")
        await db.commit()
    await ack(first)
    await clear_cooldown()
    assert await claim() is None
    async with AsyncSessionLocal() as db:
        detail = await BroadcastService(db).detail(await db.get(Broadcast, job.id))
        assert detail["status"] == "cancelled"
        assert detail["counts"] == {"sent": 1, "cancelled": 1}
        with pytest.raises(BroadcastConflict):
            await BroadcastService(db).control(job.id, records.admin.telegram_id, "resume")


@pytest.mark.asyncio
async def test_expired_claim_becomes_uncertain_and_only_pending_resumes(records):
    job = await make_job(records)
    first = await claim()
    async with AsyncSessionLocal() as db:
        item = await db.get(BroadcastRecipient, first["recipient_id"])
        item.lease_until = now_utc()-timedelta(seconds=1)
        await db.commit()
    assert await claim() is None
    assert await ack(first, "retry", retry_after=1, error_code="flood_wait") == {"status": "uncertain"}
    async with AsyncSessionLocal() as db:
        assert (await db.get(Broadcast, job.id)).status == "paused"
        await BroadcastService(db).control(job.id, records.admin.telegram_id, "resume")
        await db.commit()
    second = await claim()
    assert second["recipient_id"] != first["recipient_id"]
    assert await ack(first) == {"status": "sent"}  # A late definite success resolves the report.
    await ack(second)


@pytest.mark.asyncio
async def test_ack_committing_during_expiry_scan_is_not_overwritten(records):
    job = await make_job(records, indices=(1,))
    delivery = await claim()
    async with AsyncSessionLocal() as db:
        await db.execute(update(BroadcastRecipient).where(BroadcastRecipient.id == delivery["recipient_id"])
                         .values(lease_until=now_utc()-timedelta(seconds=1)))
        await db.commit()
    scanned, proceed = asyncio.Event(), asyncio.Event()
    class CoordinatedService(BroadcastService):
        async def load(self, broadcast_id, *, lock=False):
            scanned.set()
            await proceed.wait()
            return await super().load(broadcast_id, lock=lock)
    async def reclaim():
        async with AsyncSessionLocal() as db:
            await CoordinatedService(db).claim()
            await db.commit()
    task = asyncio.create_task(reclaim())
    try:
        await asyncio.wait_for(scanned.wait(), 5)
        await ack(delivery)
    finally:
        proceed.set()
        await asyncio.wait_for(task, 5)
    async with AsyncSessionLocal() as db:
        assert (await db.get(BroadcastRecipient, delivery["recipient_id"])).status == "sent"
        assert (await db.get(Broadcast, job.id)).status != "paused"


@pytest.mark.asyncio
async def test_definite_flood_wait_has_bounded_retries_and_global_cooldown(records):
    first_job = await make_job(records, indices=(1,))
    for attempt in range(5):
        delivery = await claim()
        result = await ack(delivery, "retry", retry_after=30, error_code="flood_wait")
        assert result["status"] == ("pending" if attempt < 4 else "failed")
        assert await claim() is None
        if attempt < 4:
            await clear_cooldown()
    await make_job(records, indices=(2,))
    async with AsyncSessionLocal() as db:
        await BroadcastService(db).control(first_job.id, records.admin.telegram_id, "cancel")
        await db.commit()
    assert await claim() is None  # Cancelling one campaign cannot bypass Telegram's token-wide 429.
    await clear_cooldown()
    assert (await claim())["telegram_id"] == records.users[2].telegram_id


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,code", [("uncertain", "transport_uncertain"), ("failed", "invalid_content")])
async def test_ambiguous_delivery_or_invalid_content_pauses_without_replay(records, outcome, code):
    job = await make_job(records)
    first = await claim()
    await ack(first, outcome, error_code=code)
    await clear_cooldown()
    assert await claim() is None
    async with AsyncSessionLocal() as db:
        assert (await db.get(Broadcast, job.id)).status == "paused"
        assert (await db.get(BroadcastRecipient, first["recipient_id"])).status == outcome


@pytest.mark.asyncio
async def test_current_language_membership_and_permission_are_rechecked(records):
    job = await make_job(records, language="fa")
    async with AsyncSessionLocal() as db:
        user = await db.get(User, records.users[1].id)
        user.preferred_language = "en"
        await db.commit()
    assert await claim() is None
    async with AsyncSessionLocal() as db:
        assert (await BroadcastService(db).detail(await db.get(Broadcast, job.id)))["counts"] == {"skipped": 1}
    job = await make_job(records)
    async with AsyncSessionLocal() as db:
        account = await db.scalar(select(AdminAccount).where(AdminAccount.user_id == records.admin.id))
        account.is_active = False
        await db.commit()
    assert await claim() is None
    async with AsyncSessionLocal() as db:
        stored = await db.get(Broadcast, job.id)
        assert (stored.status, stored.last_error) == ("paused", "permission_revoked")


@pytest.mark.asyncio
async def test_restoring_old_snapshot_cancels_pending_and_never_resends(records):
    job = await make_job(records)
    await claim()
    draft = await make_job(records, confirmed=False)
    async with AsyncSessionLocal() as db:
        result = await cancel_restored_broadcasts(db)
        await db.commit()
        assert result == {"broadcasts_cancelled": 2, "broadcast_deliveries_cancelled": 3,
                          "broadcast_deliveries_uncertain": 1}
    assert await claim() is None
    async with AsyncSessionLocal() as db:
        for job_id in (job.id, draft.id):
            with pytest.raises(BroadcastConflict):
                await BroadcastService(db).control(job_id, records.admin.telegram_id, "resume")


@pytest.mark.asyncio
async def test_api_requires_internal_key_and_admin_permission_for_reads_and_mutations(records, monkeypatch):
    monkeypatch.setattr(settings, "bot_backend_api_key", "b" * 64)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/internal/broadcasts/claim")).status_code == 401
        client.headers["X-Internal-API-Key"] = "b" * 64
        outsider = records.users[1].telegram_id
        for path in ("/admin/broadcasts", "/admin/broadcasts/plans", "/admin/broadcasts/1", "/admin/broadcasts/1/issues"):
            assert (await client.get(path, params={"actor_telegram_id": outsider})).status_code == 403
        payload = request(records).model_dump(mode="json")
        payload["actor_telegram_id"] = outsider
        assert (await client.post("/admin/broadcasts", json=payload)).status_code == 403
        for action in ("pause", "resume", "cancel", "confirm"):
            assert (await client.post(f"/admin/broadcasts/1/{action}", json={
                "actor_telegram_id": outsider, "confirmation_token": str(uuid.uuid4())})).status_code == 403
        payload["actor_telegram_id"] = records.admin.telegram_id
        response = await client.post("/admin/broadcasts", json=payload)
        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "draft"
        assert (await client.post("/internal/broadcasts/claim")).json() is None
        confirmation = {"actor_telegram_id": records.admin.telegram_id, "confirmation_token": job["confirmation_token"]}
        assert (await client.post(f"/admin/broadcasts/{job['id']}/confirm", json=confirmation)).json()["status"] == "queued"


@pytest.mark.parametrize("changes", [
    {"language": "all"}, {"segment": "plan"}, {"telegram_ids": [-100]},
    {"variants": {"fa": {"kind": "forward", "source_chat_id": 99, "source_message_id": 10}}},
    {"variants": {"fa": {"kind": "text", "text": "😀" * 2049}}},
    {"variants": {"fa": {"kind": "photo", "file_id": "a", "text": "x" * 1025}}},
])
def test_invalid_broadcast_payloads_cannot_enter_queue(changes):
    with pytest.raises(ValidationError):
        BroadcastCreate(**{"actor_telegram_id": 42, "request_id": uuid.uuid4(), "segment": "manual", "language": "fa",
                           "telegram_ids": [42], "variants": {"fa": {"kind": "text", "text": "Hello"}}, **changes})


def test_only_definite_rejection_allows_automatic_retry():
    with pytest.raises(ValidationError):
        BroadcastAck(claim_token=uuid.uuid4(), outcome="retry", retry_after=5, error_code="transport_uncertain")
    with pytest.raises(ValidationError):
        BroadcastAck(claim_token=uuid.uuid4(), outcome="sent")
