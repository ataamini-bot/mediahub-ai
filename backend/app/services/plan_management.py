import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan import Plan
from app.services.audit import AuditService


PERSIAN_ARABIC_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)
ALLOWED_QUALITIES = frozenset(
    {144, 240, 360, 480, 720, 1080, 1440, 2160}
)


class PlanManagementError(ValueError):
    code = "plan_management_error"


class PlanNotFound(PlanManagementError):
    code = "plan_not_found"


class PlanConflict(PlanManagementError):
    code = "plan_conflict"


class PlanValidationError(PlanManagementError):
    code = "plan_validation"


class SystemPlanProtected(PlanManagementError):
    code = "system_plan_protected"


class PlanManagementService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def normalize_name(value: str) -> str:
        normalized = " ".join(str(value or "").split())

        if not 2 <= len(normalized) <= 100:
            raise PlanValidationError(
                "Plan name must contain 2 to 100 characters"
            )

        return normalized

    @staticmethod
    def normalize_description(value: str | None) -> str | None:
        if value is None:
            return None

        normalized = str(value).strip()

        if not normalized or normalized == "-":
            return None

        if len(normalized) > 2000:
            raise PlanValidationError(
                "Plan description cannot exceed 2000 characters"
            )

        return normalized

    @staticmethod
    def normalize_integer(value: int | str, *, field: str) -> int:
        translated = str(value).strip().translate(PERSIAN_ARABIC_DIGITS)

        if not re.fullmatch(r"[0-9]+", translated):
            raise PlanValidationError(f"{field} must be an integer")

        return int(translated)

    @classmethod
    def normalize_duration_days(cls, value: int | str) -> int:
        normalized = cls.normalize_integer(value, field="duration_days")

        if not 1 <= normalized <= 3650:
            raise PlanValidationError(
                "Plan duration must be between 1 and 3650 days"
            )

        return normalized

    @staticmethod
    def normalize_price(value: Decimal | int | str) -> Decimal:
        translated = str(value).strip().translate(PERSIAN_ARABIC_DIGITS)
        translated = translated.replace(",", "").replace("٬", "")

        try:
            price = Decimal(translated)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise PlanValidationError("Plan price must be a number") from exc

        if not price.is_finite() or price <= 0:
            raise PlanValidationError("Custom plan price must be positive")

        if price != price.to_integral_value():
            raise PlanValidationError("Toman price must be a whole number")

        if price > Decimal("9999999999"):
            raise PlanValidationError("Plan price is too large")

        return price.quantize(Decimal("0.01"))

    @classmethod
    def normalize_daily_limit(cls, value: int | str | None) -> int | None:
        if value is None:
            return None

        normalized = cls.normalize_integer(value, field="daily_download_limit")

        if normalized == 0:
            return None

        if normalized > 1_000_000:
            raise PlanValidationError("Daily output limit is too large")

        return normalized

    @classmethod
    def normalize_file_size_mb(cls, value: int | str) -> int:
        normalized = cls.normalize_integer(value, field="max_file_size_mb")

        if not 1 <= normalized <= 1900:
            raise PlanValidationError(
                "Maximum file size must be between 1 and 1900 MB"
            )

        return normalized

    @classmethod
    def normalize_quality(cls, value: int | str) -> int:
        normalized = cls.normalize_integer(value, field="max_quality")

        if normalized not in ALLOWED_QUALITIES:
            raise PlanValidationError("Unsupported maximum quality")

        return normalized

    @classmethod
    def normalize_concurrency(cls, value: int | str) -> int:
        normalized = cls.normalize_integer(
            value,
            field="max_concurrent_downloads",
        )

        if normalized not in {1, 2, 3}:
            raise PlanValidationError(
                "Concurrent downloads must be 1, 2, or 3"
            )

        return normalized

    async def list_plans(
        self,
        *,
        include_inactive: bool = True,
        include_deleted: bool = False,
    ) -> list[Plan]:
        statement = select(Plan).order_by(Plan.sort_order, Plan.id)

        if not include_inactive:
            statement = statement.where(Plan.is_active.is_(True))

        if not include_deleted:
            statement = statement.where(Plan.deleted_at.is_(None))

        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_plan(
        self,
        plan_id: int,
        *,
        include_deleted: bool = False,
    ) -> Plan:
        statement = select(Plan).where(Plan.id == plan_id)

        if not include_deleted:
            statement = statement.where(Plan.deleted_at.is_(None))

        result = await self.session.execute(statement)
        plan = result.scalar_one_or_none()

        if plan is None:
            raise PlanNotFound("Plan not found")

        return plan

    async def create_plan(
        self,
        *,
        actor_user_id: int,
        actor_telegram_id: int,
        reason: str,
        name: str,
        description: str | None,
        duration_days: int,
        price: Decimal,
        daily_download_limit: int | None,
        max_file_size_mb: int,
        max_quality: int,
        max_concurrent_downloads: int,
        priority_processing: bool,
        forced_join_required: bool,
        sort_order: int,
        is_active: bool,
    ) -> Plan:
        normalized_name = self.normalize_name(name)
        await self._ensure_name_available(normalized_name)
        normalized_daily_limit = self.normalize_daily_limit(
            daily_download_limit
        )
        plan = Plan(
            name=normalized_name,
            slug=f"plan_{uuid.uuid4().hex[:20]}",
            description=self.normalize_description(description),
            price=self.normalize_price(price),
            duration_days=self.normalize_duration_days(duration_days),
            daily_download_limit=normalized_daily_limit,
            max_file_size_mb=self.normalize_file_size_mb(max_file_size_mb),
            max_quality=self.normalize_quality(max_quality),
            ai_enabled=False,
            priority_processing=priority_processing,
            is_unlimited=normalized_daily_limit is None,
            max_concurrent_downloads=self.normalize_concurrency(
                max_concurrent_downloads
            ),
            forced_join_required=forced_join_required,
            sort_order=sort_order,
            is_system=False,
            is_active=is_active,
        )
        self.session.add(plan)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise PlanConflict("Plan name or identifier already exists") from exc

        self._record_audit(
            action="plan.created",
            plan=plan,
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            reason=reason,
        )
        return plan

    async def update_plan(
        self,
        *,
        plan_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
        reason: str,
        name: str | None = None,
        description: str | None = None,
        description_supplied: bool = False,
        duration_days: int | None = None,
        price: Decimal | None = None,
        daily_download_limit: int | None = None,
        daily_limit_supplied: bool = False,
        max_file_size_mb: int | None = None,
        max_quality: int | None = None,
        max_concurrent_downloads: int | None = None,
        priority_processing: bool | None = None,
        forced_join_required: bool | None = None,
        sort_order: int | None = None,
        is_active: bool | None = None,
        is_deleted: bool | None = None,
    ) -> Plan:
        result = await self.session.execute(
            select(Plan).where(Plan.id == plan_id).with_for_update()
        )
        plan = result.scalar_one_or_none()

        if plan is None:
            raise PlanNotFound("Plan not found")

        if plan.is_system and any(
            (
                name is not None,
                description_supplied,
                duration_days is not None,
                price is not None,
                is_active is not None,
                is_deleted is not None,
            )
        ):
            raise SystemPlanProtected(
                "The Free plan identity, description, price, and availability "
                "are protected"
            )

        if name is not None:
            normalized_name = self.normalize_name(name)
            await self._ensure_name_available(
                normalized_name,
                exclude_plan_id=plan.id,
            )
            plan.name = normalized_name

        if description_supplied:
            plan.description = self.normalize_description(description)

        if duration_days is not None:
            plan.duration_days = self.normalize_duration_days(duration_days)

        if price is not None:
            plan.price = self.normalize_price(price)

        if daily_limit_supplied:
            normalized_limit = self.normalize_daily_limit(
                daily_download_limit
            )
            plan.daily_download_limit = normalized_limit
            plan.is_unlimited = normalized_limit is None

        if max_file_size_mb is not None:
            plan.max_file_size_mb = self.normalize_file_size_mb(
                max_file_size_mb
            )

        if max_quality is not None:
            plan.max_quality = self.normalize_quality(max_quality)

        if max_concurrent_downloads is not None:
            plan.max_concurrent_downloads = self.normalize_concurrency(
                max_concurrent_downloads
            )

        if priority_processing is not None:
            plan.priority_processing = priority_processing

        if forced_join_required is not None:
            plan.forced_join_required = forced_join_required

        if sort_order is not None:
            plan.sort_order = sort_order

        if is_active is not None:
            plan.is_active = is_active

        if is_deleted is not None:
            plan.deleted_at = datetime.now(timezone.utc) if is_deleted else None
            if is_deleted:
                plan.is_active = False

        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise PlanConflict("Plan name or identifier already exists") from exc

        self._record_audit(
            action="plan.updated",
            plan=plan,
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            reason=reason,
        )
        return plan

    async def _ensure_name_available(
        self,
        name: str,
        *,
        exclude_plan_id: int | None = None,
    ) -> None:
        statement = select(Plan.id).where(
            func.lower(Plan.name) == name.lower(),
            Plan.deleted_at.is_(None),
        )

        if exclude_plan_id is not None:
            statement = statement.where(Plan.id != exclude_plan_id)

        result = await self.session.execute(statement.limit(1))

        if result.scalar_one_or_none() is not None:
            raise PlanConflict("An active plan with this name already exists")

    def _record_audit(
        self,
        *,
        action: str,
        plan: Plan,
        actor_user_id: int,
        actor_telegram_id: int,
        reason: str,
    ) -> None:
        AuditService(self.session).record(
            action=action,
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            target_type="plan",
            target_id=plan.id,
            details={
                "reason": str(reason).strip(),
                "name": plan.name,
                "slug": plan.slug,
                "duration_days": plan.duration_days,
                "price_irt": str(plan.price),
                "daily_download_limit": plan.daily_download_limit,
                "max_file_size_mb": plan.max_file_size_mb,
                "max_quality": plan.max_quality,
                "max_concurrent_downloads": plan.max_concurrent_downloads,
                "priority_processing": plan.priority_processing,
                "forced_join_required": plan.forced_join_required,
                "is_active": plan.is_active,
                "is_deleted": plan.deleted_at is not None,
            },
        )
