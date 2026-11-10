"""Reserve coupon capacity before showing a discounted manual-payment amount."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from uuid import UUID

from sqlalchemy import Numeric, cast, func, select, text

from app.models.coupon import Coupon, CouponAction, CouponUse
from app.models.plan import Plan
from app.models.user import User
from app.schemas.coupon import CouponTerms
from app.services.admin_access import AdminAccessService
from app.services.audit import AuditService
from app.services.customers import digest


class CouponError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def utcnow():
    return datetime.now(timezone.utc)


def coupon_price(coupon, amount):
    unit = Decimal("1") if coupon.currency == "IRT" else Decimal("0.0001")
    original = Decimal(amount).quantize(unit, rounding=ROUND_CEILING)
    discount = original * coupon.value / 100 if coupon.kind == "percent" else coupon.value
    if coupon.max_discount is not None:
        discount = min(discount, coupon.max_discount)
    if discount >= original:
        raise CouponError("coupon_zero_total")
    final = (original - discount).quantize(unit, rounding=ROUND_CEILING)
    if final >= original:
        raise CouponError("coupon_no_discount")
    return {"coupon_id": str(coupon.id), "code": coupon.code, "version": coupon.version,
            "currency": coupon.currency, "kind": coupon.kind, "value": str(coupon.value),
            "original_price": str(original), "discount_amount": str(original-final), "final_price": str(final)}


class CouponService:
    def __init__(self, db):
        self.db = db

    async def get(self, coupon_id, *, lock=False):
        query = select(Coupon).where(Coupon.id == coupon_id).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update()
        row = await self.db.scalar(query)
        if row is None:
            raise LookupError("coupon_not_found")
        return row

    async def counts(self, coupon_id, user_id=None):
        query = select(CouponUse.status, func.count()).where(CouponUse.coupon_id == coupon_id)
        if user_id is not None:
            query = query.where(CouponUse.user_id == user_id)
        return dict((await self.db.execute(query.group_by(CouponUse.status))).all())

    async def check_capacity(self, coupon, user_id):
        counts = await self.counts(coupon.id)
        if coupon.total_limit is not None and counts.get("reserved", 0) + counts.get("redeemed", 0) >= coupon.total_limit:
            raise CouponError("coupon_total_limit")
        counts = await self.counts(coupon.id, user_id)
        if coupon.per_user_limit is not None and counts.get("reserved", 0) + counts.get("redeemed", 0) >= coupon.per_user_limit:
            raise CouponError("coupon_user_limit")

    async def eligible(self, code, currency, user_id, offer):
        # This row lock serializes reservations across different customers.
        coupon = await self.db.scalar(select(Coupon).where(Coupon.code == code, Coupon.currency == currency)
            .with_for_update().execution_options(populate_existing=True))
        if coupon is None:
            raise CouponError("coupon_not_found")
        now = utcnow()
        if not coupon.active:
            raise CouponError("coupon_inactive")
        if coupon.starts_at is not None and now < coupon.starts_at:
            raise CouponError("coupon_not_started")
        if coupon.expires_at is not None and now >= coupon.expires_at:
            raise CouponError("coupon_expired")
        if coupon.plan_ids and offer.plan_id not in coupon.plan_ids:
            raise CouponError("coupon_plan_scope")
        if coupon.duration_days and offer.duration_days not in coupon.duration_days:
            raise CouponError("coupon_duration_scope")
        await self.check_capacity(coupon, user_id)
        return coupon, coupon_price(coupon, offer.price)

    async def quote(self, data):
        from app.services.payment_orders import PaymentOrderService
        from app.services.payment_offers import get_payment_offer
        from app.services.managed_settings import ensure_public_operation
        orders = PaymentOrderService(self.db)
        user = await orders.user(data.telegram_id)
        orders.require_currency(user, data.currency)
        await ensure_public_operation(self.db, "payments")
        try:
            offer = await get_payment_offer(self.db, data.offer_code, currency=data.currency)
        except LookupError as exc:
            raise CouponError("coupon_offer_unavailable") from exc
        _, snapshot = await self.eligible(data.coupon_code, data.currency, user.id, offer)
        return snapshot

    async def reserve(self, order, snapshot, actor):
        # Caller still holds the coupon lock obtained by eligible().
        self.db.add(CouponUse(order_id=order.id, coupon_id=UUID(snapshot["coupon_id"]),
            user_id=order.user_id, snapshot=snapshot, status="reserved"))
        AuditService(self.db).record(action="coupon.reserved", actor_telegram_id=actor,
            target_type="payment_order", target_id=str(order.id), details=snapshot)
        await self.db.flush()

    async def transition(self, order_id, status, *, actor, payment_id=None):
        if order_id is None:
            return
        use = await self.db.get(CouponUse, order_id)
        if use is None:
            return
        await self.get(use.coupon_id, lock=True)
        await self.db.refresh(use)
        if use.status == status:
            return
        if use.status != "reserved":
            raise CouponError("coupon_reservation_closed")
        use.status = status
        use.payment_id = payment_id
        AuditService(self.db).record(action=f"coupon.{status}", actor_telegram_id=actor,
            target_type="payment_order", target_id=str(order_id),
            details={"coupon_id": str(use.coupon_id), "payment_id": payment_id})
        await self.db.flush()

    async def attach_payment(self, order_id, payment_id):
        if order_id is None:
            return
        use = await self.db.get(CouponUse, order_id)
        if use is not None:
            await self.get(use.coupon_id, lock=True)
            await self.db.refresh(use)
            if use.status != "reserved":
                raise CouponError("coupon_reservation_closed")
            use.payment_id = payment_id
            await self.db.flush()

    async def change(self, data, coupon_id=None):
        await AdminAccessService(self.db).require_permission(data.actor_telegram_id, "coupons.manage")
        # Serialize identical administrative request IDs, including create retries.
        await self.db.execute(text("SELECT pg_advisory_xact_lock(740218, :key)"), {"key": data.request_id.int & 0x7fffffff})
        payload_hash = digest({"coupon_id": str(coupon_id) if coupon_id else None,
                               **data.model_dump(mode="json", exclude={"request_id"})})
        prior = await self.db.get(CouponAction, data.request_id)
        if prior:
            if prior.payload_hash != payload_hash:
                raise CouponError("coupon_request_reused")
            return await self.detail(prior.coupon_id)
        terms = data.terms
        if terms.plan_ids:
            plans = (await self.db.scalars(select(Plan).where(Plan.id.in_(terms.plan_ids),
                Plan.is_system.is_(False), Plan.deleted_at.is_(None)))).all()
            if len(plans) != len(terms.plan_ids):
                raise CouponError("coupon_plan_scope")
        if coupon_id is None:
            if data.expected_version != 0:
                raise CouponError("coupon_stale")
            coupon = Coupon(id=data.request_id, **terms.model_dump(), version=1)
            self.db.add(coupon)
        else:
            coupon = await self.get(coupon_id, lock=True)
            if coupon.version != data.expected_version:
                raise CouponError("coupon_stale")
            # Existing reservations keep their monetary and scope snapshots.
            # Keep identity/currency stable so capacity and reports stay meaningful.
            if terms.code != coupon.code or terms.currency != coupon.currency:
                raise CouponError("coupon_identity_immutable")
            counts = await self.counts(coupon.id)
            occupied = counts.get("reserved", 0) + counts.get("redeemed", 0)
            if terms.total_limit is not None and terms.total_limit < occupied:
                raise CouponError("coupon_limit_below_usage")
            if terms.per_user_limit is not None:
                usage = select(func.count().label("uses")).where(CouponUse.coupon_id == coupon.id,
                    CouponUse.status.in_(["reserved", "redeemed"])).group_by(CouponUse.user_id).subquery()
                maximum = await self.db.scalar(select(func.max(usage.c.uses)))
                if maximum and terms.per_user_limit < maximum:
                    raise CouponError("coupon_limit_below_usage")
            for key, value in terms.model_dump().items():
                setattr(coupon, key, value)
            coupon.version += 1
        await self.db.flush()
        self.db.add(CouponAction(id=data.request_id, coupon_id=coupon.id, payload_hash=payload_hash))
        AuditService(self.db).record(action="coupon.created" if coupon_id is None else "coupon.updated",
            actor_telegram_id=data.actor_telegram_id, target_type="coupon", target_id=str(coupon.id),
            details={"reason": data.reason, "version": coupon.version, "terms": terms.model_dump(mode="json")})
        await self.db.flush()
        return await self.detail(coupon.id)

    async def detail(self, coupon_id):
        coupon = await self.get(coupon_id)
        terms = CouponTerms(**{key: getattr(coupon, key) for key in CouponTerms.model_fields}).model_dump(mode="json")
        uses = (await self.db.execute(select(CouponUse, User.telegram_id).join(User, User.id == CouponUse.user_id)
            .where(CouponUse.coupon_id == coupon.id).order_by(CouponUse.created_at.desc()).limit(10))).all()
        totals = (await self.db.execute(select(*[
            func.coalesce(func.sum(cast(CouponUse.snapshot[key].as_string(), Numeric(24, 4))), 0)
            for key in ("discount_amount", "final_price")
        ]).where(CouponUse.coupon_id == coupon.id, CouponUse.status == "redeemed"))).one()
        plans = (await self.db.scalars(select(Plan).where(Plan.id.in_(coupon.plan_ids)))).all() if coupon.plan_ids else []
        return {"id": str(coupon.id), "version": coupon.version, "terms": terms,
                "counts": await self.counts(coupon.id),
                "redeemed_discount": str(totals[0]), "redeemed_amount": str(totals[1]),
                "plans": [{"id": p.id, "name": p.name, "name_en": p.name_en} for p in plans],
                "recent": [{"order_id": str(row.order_id), "telegram_id": tid, "status": row.status,
                    "payment_id": row.payment_id, "created_at": row.created_at.isoformat(),
                    "snapshot": row.snapshot} for row, tid in uses]}

    async def listing(self, page=1):
        coupons = (await self.db.scalars(select(Coupon).order_by(Coupon.created_at.desc(), Coupon.id)
            .offset((page-1)*8).limit(8))).all()
        return {"page": page, "total": await self.db.scalar(select(func.count()).select_from(Coupon)),
            "items": [{"id": str(c.id), "code": c.code, "currency": c.currency, "active": c.active,
                       "counts": await self.counts(c.id)} for c in coupons]}

    async def options(self):
        rows = (await self.db.scalars(select(Plan).where(Plan.is_system.is_(False), Plan.deleted_at.is_(None))
                                      .order_by(Plan.sort_order, Plan.id))).all()
        return [{"id": p.id, "name": p.name, "name_en": p.name_en, "duration_days": p.duration_days,
                 "active": p.is_active} for p in rows]
