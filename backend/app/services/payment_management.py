import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.payment import Payment, PaymentStatus
from app.models.payment_destination import PaymentCard, UsdtDestination
from app.models.user import User
from app.services.audit import AuditService


PAYMENT_CARD_ROTATION_LOCK_ID = 6_148_327_401


class PaymentManagementError(RuntimeError):
    pass


class PaymentDestinationNotFound(LookupError):
    pass


class PaymentDestinationConflict(PaymentManagementError):
    pass


class PaymentDestinationValidation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AdminPaymentRecord:
    payment: Payment
    user: User


@dataclass(frozen=True, slots=True)
class AdminPaymentPage:
    items: tuple[AdminPaymentRecord, ...]
    total: int
    page: int
    page_size: int


def normalize_card_number(value: str) -> str:
    raw = str(value or "").strip()
    normalized = re.sub(r"[\s-]+", "", raw)
    if not normalized.isdigit() or len(normalized) != 16:
        raise PaymentDestinationValidation(
            "Card number must contain exactly 16 digits"
        )
    return normalized


def payment_card_snapshot(card: PaymentCard) -> dict[str, Any]:
    return {
        "type": "card",
        "id": card.id,
        "label": card.label,
        "card_number": card.card_number,
        "card_holder": card.card_holder,
        "bank_name": card.bank_name,
    }


def legacy_payment_card_snapshot() -> dict[str, Any]:
    number = settings.payment_card_number.strip()
    holder = settings.payment_card_holder.strip()
    if not number or not holder:
        raise PaymentDestinationValidation(
            "No active database card or legacy payment card is configured"
        )
    return {
        "type": "card",
        "id": None,
        "label": "Legacy environment card",
        "card_number": number,
        "card_holder": holder,
        "bank_name": settings.payment_bank_name.strip() or None,
    }


class PaymentManagementService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def summary(self) -> dict[str, int | bool]:
        result = await self.session.execute(
            select(Payment.status, func.count(Payment.id)).group_by(
                Payment.status
            )
        )
        counts = {status: int(count) for status, count in result.all()}
        card_count = int(
            (
                await self.session.execute(
                    select(func.count(PaymentCard.id))
                )
            ).scalar_one()
        )
        active_card_count = int(
            (
                await self.session.execute(
                    select(func.count(PaymentCard.id)).where(
                        PaymentCard.is_active.is_(True)
                    )
                )
            ).scalar_one()
        )
        usdt_count = int(
            (
                await self.session.execute(
                    select(func.count(UsdtDestination.id))
                )
            ).scalar_one()
        )
        active_usdt_count = int(
            (
                await self.session.execute(
                    select(func.count(UsdtDestination.id)).where(
                        UsdtDestination.is_active.is_(True)
                    )
                )
            ).scalar_one()
        )
        return {
            "pending": counts.get(PaymentStatus.PENDING, 0),
            "approved": counts.get(PaymentStatus.APPROVED, 0),
            "rejected": counts.get(PaymentStatus.REJECTED, 0),
            "cards": card_count,
            "active_cards": active_card_count,
            "usdt_destinations": usdt_count,
            "active_usdt_destinations": active_usdt_count,
            "legacy_card_configured": bool(
                settings.payment_card_number.strip()
                and settings.payment_card_holder.strip()
            ),
        }

    async def list_payments(
        self,
        *,
        status: PaymentStatus | None,
        page: int,
        page_size: int,
    ) -> AdminPaymentPage:
        filters = []
        if status is not None:
            filters.append(Payment.status == status)

        total_statement = select(func.count(Payment.id))
        if filters:
            total_statement = total_statement.where(*filters)
        total = int((await self.session.execute(total_statement)).scalar_one())

        statement = (
            select(Payment, User)
            .join(User, User.id == Payment.user_id)
            .order_by(Payment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if filters:
            statement = statement.where(*filters)

        result = await self.session.execute(statement)
        return AdminPaymentPage(
            items=tuple(
                AdminPaymentRecord(payment=payment, user=user)
                for payment, user in result.all()
            ),
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_payment(self, payment_id: int) -> AdminPaymentRecord:
        result = await self.session.execute(
            select(Payment, User)
            .join(User, User.id == Payment.user_id)
            .where(Payment.id == payment_id)
        )
        row = result.first()
        if row is None:
            raise PaymentDestinationNotFound("Payment not found")
        payment, user = row
        return AdminPaymentRecord(payment=payment, user=user)

    async def list_cards(self) -> list[PaymentCard]:
        result = await self.session.execute(
            select(PaymentCard).order_by(
                PaymentCard.sort_order,
                PaymentCard.id,
            )
        )
        return list(result.scalars())

    async def has_database_cards(self) -> bool:
        result = await self.session.execute(
            select(PaymentCard.id).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_card(self, card_id: int) -> PaymentCard:
        result = await self.session.execute(
            select(PaymentCard).where(PaymentCard.id == card_id)
        )
        card = result.scalar_one_or_none()
        if card is None:
            raise PaymentDestinationNotFound("Payment card not found")
        return card

    async def create_card(
        self,
        *,
        actor_user_id: int,
        actor_telegram_id: int,
        label: str,
        card_number: str,
        card_holder: str,
        bank_name: str | None,
        sort_order: int,
        is_active: bool,
    ) -> PaymentCard:
        card = PaymentCard(
            label=label.strip(),
            card_number=normalize_card_number(card_number),
            card_holder=card_holder.strip(),
            bank_name=bank_name.strip() if bank_name else None,
            sort_order=sort_order,
            is_active=is_active,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        self.session.add(card)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise PaymentDestinationConflict(
                "This payment card is already registered"
            ) from exc

        AuditService(self.session).record(
            action="payment_card.created",
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            target_type="payment_card",
            target_id=card.id,
            details={
                "label": card.label,
                "last_four": card.card_number[-4:],
                "is_active": card.is_active,
            },
        )
        return card

    async def update_card(
        self,
        *,
        card_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
        changes: dict[str, Any],
    ) -> PaymentCard:
        result = await self.session.execute(
            select(PaymentCard)
            .where(PaymentCard.id == card_id)
            .with_for_update()
        )
        card = result.scalar_one_or_none()
        if card is None:
            raise PaymentDestinationNotFound("Payment card not found")

        for field, value in changes.items():
            if field == "card_number":
                value = normalize_card_number(str(value))
            elif field in {"label", "card_holder"}:
                value = str(value).strip()
            elif field == "bank_name":
                value = str(value).strip() if value else None
            setattr(card, field, value)
        card.updated_by_user_id = actor_user_id

        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise PaymentDestinationConflict(
                "This payment card is already registered"
            ) from exc

        AuditService(self.session).record(
            action="payment_card.updated",
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            target_type="payment_card",
            target_id=card.id,
            details={
                "fields": sorted(changes),
                "last_four": card.card_number[-4:],
            },
        )
        return card

    async def delete_card(
        self,
        *,
        card_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
    ) -> None:
        card = await self.get_card(card_id)
        last_four = card.card_number[-4:]
        label = card.label
        await self.session.execute(
            delete(PaymentCard).where(PaymentCard.id == card_id)
        )
        AuditService(self.session).record(
            action="payment_card.deleted",
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            target_type="payment_card",
            target_id=card_id,
            details={"label": label, "last_four": last_four},
        )

    async def select_card(self) -> PaymentCard | None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": PAYMENT_CARD_ROTATION_LOCK_ID},
        )
        result = await self.session.execute(
            select(PaymentCard)
            .where(PaymentCard.is_active.is_(True))
            .order_by(
                PaymentCard.selection_count,
                PaymentCard.last_selected_at.asc().nullsfirst(),
                PaymentCard.sort_order,
                PaymentCard.id,
            )
            .limit(1)
            .with_for_update()
        )
        card = result.scalar_one_or_none()
        if card is not None:
            card.selection_count += 1
            card.last_selected_at = datetime.now(timezone.utc)
            await self.session.flush()
        return card

    async def payment_card_for_submission(
        self,
        card_id: int | None,
    ) -> tuple[int | None, dict[str, Any]]:
        if card_id is None:
            return None, legacy_payment_card_snapshot()
        card = await self.get_card(card_id)
        if not card.is_active:
            raise PaymentDestinationValidation(
                "Selected payment card is no longer active"
            )
        return card.id, payment_card_snapshot(card)

    async def list_usdt_destinations(self) -> list[UsdtDestination]:
        result = await self.session.execute(
            select(UsdtDestination).order_by(
                UsdtDestination.sort_order,
                UsdtDestination.id,
            )
        )
        return list(result.scalars())

    async def get_usdt_destination(
        self,
        destination_id: int,
    ) -> UsdtDestination:
        result = await self.session.execute(
            select(UsdtDestination).where(UsdtDestination.id == destination_id)
        )
        destination = result.scalar_one_or_none()
        if destination is None:
            raise PaymentDestinationNotFound("USDT destination not found")
        return destination

    async def create_usdt_destination(
        self,
        *,
        actor_user_id: int,
        actor_telegram_id: int,
        data: dict[str, Any],
    ) -> UsdtDestination:
        destination = UsdtDestination(
            **data,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        self.session.add(destination)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise PaymentDestinationConflict(
                "This network and address are already registered"
            ) from exc

        AuditService(self.session).record(
            action="usdt_destination.created",
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            target_type="usdt_destination",
            target_id=destination.id,
            details={
                "network_code": destination.network_code,
                "is_active": destination.is_active,
            },
        )
        return destination

    async def update_usdt_destination(
        self,
        *,
        destination_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
        changes: dict[str, Any],
    ) -> UsdtDestination:
        result = await self.session.execute(
            select(UsdtDestination)
            .where(UsdtDestination.id == destination_id)
            .with_for_update()
        )
        destination = result.scalar_one_or_none()
        if destination is None:
            raise PaymentDestinationNotFound("USDT destination not found")

        for field, value in changes.items():
            setattr(destination, field, value)
        destination.updated_by_user_id = actor_user_id
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise PaymentDestinationConflict(
                "This network and address are already registered"
            ) from exc

        AuditService(self.session).record(
            action="usdt_destination.updated",
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            target_type="usdt_destination",
            target_id=destination.id,
            details={
                "fields": sorted(changes),
                "network_code": destination.network_code,
            },
        )
        return destination

    async def delete_usdt_destination(
        self,
        *,
        destination_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
    ) -> None:
        destination = await self.get_usdt_destination(destination_id)
        network_code = destination.network_code
        await self.session.execute(
            delete(UsdtDestination).where(UsdtDestination.id == destination_id)
        )
        AuditService(self.session).record(
            action="usdt_destination.deleted",
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            target_type="usdt_destination",
            target_id=destination_id,
            details={"network_code": network_code},
        )
