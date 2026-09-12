from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import distinct, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import (
    AdminAccount,
    AdminPermission,
    AdminRole,
    AdminRoleAssignment,
    AdminRolePermission,
)
from app.models.bot_experience import (
    HomeButton,
    RequiredChannel,
    SupportMessage,
    SupportTicket,
    SupportTicketEvent,
)
from app.models.plan import Plan
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User, UserStatus
from app.services.application_settings import ApplicationSettingsService
from app.services.audit import AuditService
from app.services.admin_access import AdminAccessService
from app.core.language import effective_language


SUPPORTED_LANGUAGES = {"fa", "en"}
HOME_BUTTON_ACTIONS = {
    "url",
    "message",
    "buy",
    "subscription",
    "support",
    "tutorial",
    "faq",
}
BUTTON_STYLES = {"default", "primary", "success", "danger"}
SUPPORT_CATEGORIES = {"download", "payment", "subscription", "account", "other"}
SUPPORT_FILE_TYPES = {"photo", "document", "video", "voice"}
SUPPORT_STATUSES = {"new", "in_progress", "waiting_user", "answered", "closed"}
OPEN_SUPPORT_STATUSES = SUPPORT_STATUSES - {"closed"}


DEFAULT_CONTENT: dict[str, dict[str, str]] = {
    "fa": {
        "welcome_title": "👋 به MediaHub AI خوش آمدید!",
        "welcome_instruction": "🎬 لینک رسانه را ارسال کنید تا بررسی شود.",
        "tutorial": (
            "📘 آموزش استفاده از ربات\n\n"
            "1) لینک پست یا ویدئو را از شبکه اجتماعی کپی کنید.\n"
            "2) لینک را برای ربات بفرستید.\n"
            "3) در صورت وجود، رسانه یا کیفیت دلخواه را انتخاب کنید.\n"
            "4) تا پایان پردازش صبر کنید؛ فایل در همین گفتگو ارسال می‌شود.\n\n"
            "اگر سهمیه یا کیفیت پلن کافی نبود، از بخش خرید اشتراک استفاده کنید."
        ),
        "faq": (
            "❓ سوالات متداول\n\n"
            "• چرا بعضی لینک‌ها دانلود نمی‌شوند؟\n"
            "محتوای خصوصی، حذف‌شده، محدود به سن/کشور یا نیازمند ورود قابل دریافت نیست.\n\n"
            "• چرا حجم نهایی کمی متفاوت است؟\n"
            "در پخش‌های چندبخشی حجم قبل از دانلود برآورد می‌شود و ممکن است اندکی تغییر کند.\n\n"
            "• سهمیه چه زمانی برمی‌گردد؟\n"
            "سهمیه روزانه در نیمه‌شب منطقه زمانی تنظیم‌شده بازنشانی می‌شود.\n\n"
            "• پرداخت من چه زمانی فعال می‌شود؟\n"
            "پس از بررسی رسید توسط مدیر مالی، نتیجه در همین گفتگو ارسال می‌شود."
        ),
        "support_intro": "موضوع درخواست پشتیبانی را انتخاب کنید:",
        "support_prompt": (
            "پیام خود را در یک نوبت بفرستید. متن، تصویر، ویدئو، فایل یا پیام صوتی پذیرفته می‌شود."
        ),
        "support_sent": "✅ درخواست شما ثبت شد و برای مدیران مرتبط ارسال شد.",
        "forced_join": (
            "برای استفاده از دانلود، ابتدا در کانال‌های زیر عضو شوید و سپس «بررسی عضویت» را بزنید."
        ),
        "membership_verified": "✅ عضویت شما تأیید شد؛ اکنون لینک را دوباره بفرستید.",
    },
    "en": {
        "welcome_title": "👋 Welcome to MediaHub AI!",
        "welcome_instruction": "🎬 Send a media link and I will inspect it.",
        "tutorial": (
            "📘 How to use the bot\n\n"
            "1) Copy a post or video link from a supported platform.\n"
            "2) Send the link to the bot.\n"
            "3) Choose an item or quality when offered.\n"
            "4) Wait for processing; the file will be delivered here.\n\n"
            "Use Buy subscription if your plan limit is not sufficient."
        ),
        "faq": (
            "❓ Frequently asked questions\n\n"
            "• Why can some links not be downloaded?\n"
            "Private, removed, age/region-restricted, or login-only media is unavailable.\n\n"
            "• Why can the final size differ slightly?\n"
            "Segmented streams are estimated before download and may vary slightly.\n\n"
            "• When does my quota reset?\n"
            "Daily quota resets at midnight in the configured quota timezone.\n\n"
            "• When is a payment activated?\n"
            "You will be notified here after a finance administrator reviews the receipt."
        ),
        "support_intro": "Choose the subject of your support request:",
        "support_prompt": (
            "Send your request in one message. Text, photo, video, document, and voice are accepted."
        ),
        "support_sent": "✅ Your request was recorded and sent to the relevant administrators.",
        "forced_join": "Join the channels below, then tap Check membership to use downloads.",
        "membership_verified": "✅ Membership verified. You can send the link again now.",
    },
}

DEFAULT_BUTTONS: dict[str, dict[str, str]] = {
    "fa": {
        "buy": "💎 خرید اشتراک",
        "subscription": "👤 وضعیت اشتراک من",
        "language": "🌐 تغییر زبان | Language",
        "support": "🛟 پشتیبانی",
        "tutorial": "📘 آموزش استفاده",
        "faq": "❓ سوالات متداول",
        "admin": "⚙️ پنل مدیریت",
        "check_membership": "✅ بررسی عضویت",
        "back_home": "🏠 منوی اصلی",
    },
    "en": {
        "buy": "💎 Buy subscription",
        "subscription": "👤 My subscription",
        "language": "🌐 Language | تغییر زبان",
        "support": "🛟 Support",
        "tutorial": "📘 How to use",
        "faq": "❓ FAQ",
        "admin": "⚙️ Admin panel",
        "check_membership": "✅ Check membership",
        "back_home": "🏠 Main menu",
    },
}


class BotExperienceError(ValueError):
    code = "bot_experience_error"


class BotExperienceNotFound(LookupError):
    code = "bot_experience_not_found"


class BotExperienceConflict(RuntimeError):
    code = "bot_experience_conflict"


@dataclass(slots=True)
class SupportTicketRecord:
    ticket: SupportTicket
    user: User
    messages: list[SupportMessage]
    events: list[SupportTicketEvent]
    plan_name: str | None = None


@dataclass(frozen=True, slots=True)
class SupportTicketPage:
    items: tuple[SupportTicketRecord, ...]
    total: int
    page: int
    page_size: int


def _clean_text(value: Any, *, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise BotExperienceError(f"{field} cannot be blank")
    if len(normalized) > maximum:
        raise BotExperienceError(f"{field} is too long")
    return normalized


def _normalize_https_url(value: Any, *, telegram_only: bool = False) -> str:
    normalized = _clean_text(value, field="URL", maximum=500)
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.hostname:
        raise BotExperienceError("URL must use HTTPS")
    if telegram_only and parsed.hostname.lower() not in {
        "t.me",
        "www.t.me",
        "telegram.me",
        "www.telegram.me",
    }:
        raise BotExperienceError("Channel invite URL must be a Telegram link")
    return normalized


def _normalize_chat_id(value: Any) -> str:
    normalized = _clean_text(value, field="Channel identifier", maximum=100)
    if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", normalized):
        return normalized
    if re.fullmatch(r"-100[0-9]{5,20}", normalized):
        return normalized
    raise BotExperienceError(
        "Channel identifier must be @username or a -100... Telegram chat ID"
    )


def _merge_string_map(defaults: dict[str, str], value: Any) -> dict[str, str]:
    result = dict(defaults)
    if not isinstance(value, dict):
        return result
    for key in defaults:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            result[key] = candidate.strip()
    return result


class BotExperienceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def configuration(self, language: str) -> dict[str, Any]:
        normalized = language if language in SUPPORTED_LANGUAGES else "fa"
        settings = ApplicationSettingsService(self.session)
        content = _merge_string_map(
            DEFAULT_CONTENT[normalized],
            await settings.get_value(f"bot.content.{normalized}"),
        )
        buttons = _merge_string_map(
            DEFAULT_BUTTONS[normalized],
            await settings.get_value(f"bot.buttons.{normalized}"),
        )
        home_result = await self.session.execute(
            select(HomeButton)
            .where(HomeButton.is_active.is_(True))
            .order_by(HomeButton.sort_order, HomeButton.id)
        )
        channel_result = await self.session.execute(
            select(RequiredChannel)
            .where(RequiredChannel.is_active.is_(True))
            .order_by(RequiredChannel.sort_order, RequiredChannel.id)
        )
        return {
            "language": normalized,
            "content": content,
            "buttons": buttons,
            "custom_buttons": [self.serialize_home_button(row) for row in home_result.scalars()],
            "required_channels": [self.serialize_channel(row) for row in channel_result.scalars()],
        }

    @staticmethod
    def serialize_home_button(row: HomeButton) -> dict[str, Any]:
        return {
            "id": row.id,
            "label_fa": row.label_fa,
            "label_en": row.label_en,
            "action_type": row.action_type,
            "action_value": row.action_value,
            "style": row.style,
            "sort_order": row.sort_order,
            "is_active": row.is_active,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def serialize_channel(row: RequiredChannel) -> dict[str, Any]:
        return {
            "id": row.id,
            "chat_id": row.chat_id,
            "title": row.title,
            "invite_url": row.invite_url,
            "sort_order": row.sort_order,
            "is_active": row.is_active,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def list_home_buttons(self) -> list[HomeButton]:
        result = await self.session.execute(
            select(HomeButton).order_by(HomeButton.sort_order, HomeButton.id)
        )
        return list(result.scalars())

    async def get_home_button(self, button_id: int, *, lock: bool = False) -> HomeButton:
        statement = select(HomeButton).where(HomeButton.id == button_id)
        if lock:
            statement = statement.with_for_update()
        row = (await self.session.execute(statement)).scalar_one_or_none()
        if row is None:
            raise BotExperienceNotFound("Home button not found")
        return row

    async def create_home_button(
        self,
        *,
        actor_user_id: int,
        actor_telegram_id: int,
        data: dict[str, Any],
    ) -> HomeButton:
        normalized = self._validate_home_button(data, partial=False)
        row = HomeButton(
            **normalized,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        self.session.add(row)
        await self.session.flush()
        self._audit("home_button.created", actor_user_id, actor_telegram_id, row)
        return row

    async def update_home_button(
        self,
        *,
        button_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
        changes: dict[str, Any],
    ) -> HomeButton:
        row = await self.get_home_button(button_id, lock=True)
        candidate = {
            "label_fa": row.label_fa,
            "label_en": row.label_en,
            "action_type": row.action_type,
            "action_value": row.action_value,
            "style": row.style,
            "sort_order": row.sort_order,
            "is_active": row.is_active,
            **changes,
        }
        normalized = self._validate_home_button(candidate, partial=False)
        for key, value in normalized.items():
            setattr(row, key, value)
        row.updated_by_user_id = actor_user_id
        await self.session.flush()
        self._audit("home_button.updated", actor_user_id, actor_telegram_id, row)
        return row

    async def delete_home_button(
        self,
        *,
        button_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
    ) -> None:
        row = await self.get_home_button(button_id, lock=True)
        self._audit("home_button.deleted", actor_user_id, actor_telegram_id, row)
        await self.session.delete(row)
        await self.session.flush()

    @staticmethod
    def _validate_home_button(data: dict[str, Any], *, partial: bool) -> dict[str, Any]:
        action_type = str(data.get("action_type") or "").strip().lower()
        if action_type not in HOME_BUTTON_ACTIONS:
            raise BotExperienceError("Unsupported home button action")
        action_value = str(data.get("action_value") or "").strip() or None
        if action_type == "url":
            action_value = _normalize_https_url(action_value)
        elif action_type == "message":
            action_value = _clean_text(action_value, field="Button message", maximum=3900)
        else:
            action_value = None

        style = str(data.get("style") or "default").strip().lower()
        if style not in BUTTON_STYLES:
            raise BotExperienceError("Unsupported Telegram button style")
        try:
            sort_order = int(data.get("sort_order", 100))
        except (TypeError, ValueError) as exc:
            raise BotExperienceError("Button order must be an integer") from exc
        if not 0 <= sort_order <= 100_000:
            raise BotExperienceError("Button order is out of range")
        return {
            "label_fa": _clean_text(data.get("label_fa"), field="Persian label", maximum=64),
            "label_en": _clean_text(data.get("label_en"), field="English label", maximum=64),
            "action_type": action_type,
            "action_value": action_value,
            "style": style,
            "sort_order": sort_order,
            "is_active": bool(data.get("is_active", True)),
        }

    async def list_channels(self) -> list[RequiredChannel]:
        result = await self.session.execute(
            select(RequiredChannel).order_by(RequiredChannel.sort_order, RequiredChannel.id)
        )
        return list(result.scalars())

    async def get_channel(self, channel_id: int, *, lock: bool = False) -> RequiredChannel:
        statement = select(RequiredChannel).where(RequiredChannel.id == channel_id)
        if lock:
            statement = statement.with_for_update()
        row = (await self.session.execute(statement)).scalar_one_or_none()
        if row is None:
            raise BotExperienceNotFound("Required channel not found")
        return row

    async def create_channel(
        self,
        *,
        actor_user_id: int,
        actor_telegram_id: int,
        data: dict[str, Any],
    ) -> RequiredChannel:
        normalized = self._validate_channel(data)
        row = RequiredChannel(
            **normalized,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise BotExperienceConflict("Channel is already configured") from exc
        self._audit("required_channel.created", actor_user_id, actor_telegram_id, row)
        return row

    async def update_channel(
        self,
        *,
        channel_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
        changes: dict[str, Any],
    ) -> RequiredChannel:
        row = await self.get_channel(channel_id, lock=True)
        candidate = {
            "chat_id": row.chat_id,
            "title": row.title,
            "invite_url": row.invite_url,
            "sort_order": row.sort_order,
            "is_active": row.is_active,
            **changes,
        }
        normalized = self._validate_channel(candidate)
        for key, value in normalized.items():
            setattr(row, key, value)
        row.updated_by_user_id = actor_user_id
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise BotExperienceConflict("Channel is already configured") from exc
        self._audit("required_channel.updated", actor_user_id, actor_telegram_id, row)
        return row

    async def delete_channel(
        self,
        *,
        channel_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
    ) -> None:
        row = await self.get_channel(channel_id, lock=True)
        self._audit("required_channel.deleted", actor_user_id, actor_telegram_id, row)
        await self.session.delete(row)
        await self.session.flush()

    @staticmethod
    def _validate_channel(data: dict[str, Any]) -> dict[str, Any]:
        try:
            order = int(data.get("sort_order", 0))
        except (TypeError, ValueError) as exc:
            raise BotExperienceError("Channel order must be an integer") from exc
        if not 0 <= order <= 100_000:
            raise BotExperienceError("Channel order is out of range")
        return {
            "chat_id": _normalize_chat_id(data.get("chat_id")),
            "title": _clean_text(data.get("title"), field="Channel title", maximum=120),
            "invite_url": _normalize_https_url(data.get("invite_url"), telegram_only=True),
            "sort_order": order,
            "is_active": bool(data.get("is_active", True)),
        }

    async def create_ticket(
        self,
        *,
        telegram_id: int,
        category: str,
        body: str | None,
        telegram_file_id: str | None,
        file_type: str | None,
    ) -> SupportTicketRecord:
        normalized_category = str(category or "").strip().lower()
        if normalized_category not in SUPPORT_CATEGORIES:
            raise BotExperienceError("Unknown support category")
        normalized_body = str(body or "").strip()[:3900] or None
        normalized_file_id = str(telegram_file_id or "").strip()[:512] or None
        normalized_file_type = str(file_type or "").strip().lower() or None
        if normalized_file_type not in SUPPORT_FILE_TYPES | {None}:
            raise BotExperienceError("Unsupported support attachment")
        if not normalized_body and not normalized_file_id:
            raise BotExperienceError("Support message cannot be empty")

        user = (
            await self.session.execute(
                select(User)
                .where(User.telegram_id == telegram_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if user is None:
            raise BotExperienceNotFound("Telegram user is not registered")
        open_count = int(
            (
                await self.session.execute(
                    select(func.count(SupportTicket.id)).where(
                        SupportTicket.user_id == user.id,
                        SupportTicket.status.in_(OPEN_SUPPORT_STATUSES),
                    )
                )
            ).scalar_one()
        )
        if open_count >= 3:
            raise BotExperienceConflict(
                "A user can have at most three open support tickets"
            )
        if user.status != UserStatus.ACTIVE:
            raise BotExperienceConflict("User account is not active")
        ticket = SupportTicket(user_id=user.id, category=normalized_category, status="new")
        self.session.add(ticket)
        await self.session.flush()
        message = SupportMessage(
            ticket_id=ticket.id,
            sender_user_id=user.id,
            sender_telegram_id=telegram_id,
            sender_kind="user",
            body=normalized_body,
            telegram_file_id=normalized_file_id,
            file_type=normalized_file_type,
        )
        self.session.add(message)
        event = self._ticket_event(
            ticket,
            event_type="created",
            actor_user_id=user.id,
            actor_telegram_id=telegram_id,
            to_status="new",
        )
        await self.session.flush()
        AuditService(self.session).record(
            action="support.ticket_created",
            actor_user_id=user.id,
            actor_telegram_id=telegram_id,
            target_type="support_ticket",
            target_id=ticket.id,
            details={"category": normalized_category, "file_type": normalized_file_type},
        )
        return SupportTicketRecord(
            ticket=ticket,
            user=user,
            messages=[message],
            events=[event],
            plan_name=await self._ticket_plan_name(user.id, effective_language(preferred_language=user.preferred_language, telegram_language_code=user.language_code)),
        )

    async def list_tickets(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 8,
        user_telegram_id: int | None = None,
    ) -> SupportTicketPage:
        filters = []
        if status is not None:
            if status not in SUPPORT_STATUSES:
                raise BotExperienceError("Unknown support ticket status")
            filters.append(SupportTicket.status == status)
        if user_telegram_id is not None:
            filters.append(User.telegram_id == user_telegram_id)
        total_statement = select(func.count(SupportTicket.id)).join(
            User, User.id == SupportTicket.user_id
        )
        if filters:
            total_statement = total_statement.where(*filters)
        total = int((await self.session.execute(total_statement)).scalar_one())
        statement = (
            select(SupportTicket, User)
            .join(User, User.id == SupportTicket.user_id)
            .order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc())
            .offset((max(1, page) - 1) * max(1, min(page_size, 30)))
            .limit(max(1, min(page_size, 30)))
        )
        if filters:
            statement = statement.where(*filters)
        pairs = list((await self.session.execute(statement)).all())
        records: list[SupportTicketRecord] = []
        for ticket, user in pairs:
            records.append(await self._ticket_record(ticket, user))
        return SupportTicketPage(
            items=tuple(records),
            total=total,
            page=max(1, page),
            page_size=max(1, min(page_size, 30)),
        )

    async def get_ticket(
        self,
        ticket_id: int,
        *,
        lock: bool = False,
        user_telegram_id: int | None = None,
    ) -> SupportTicketRecord:
        statement = (
            select(SupportTicket, User)
            .join(User, User.id == SupportTicket.user_id)
            .where(SupportTicket.id == ticket_id)
        )
        if user_telegram_id is not None:
            statement = statement.where(User.telegram_id == user_telegram_id)
        if lock:
            statement = statement.with_for_update(of=SupportTicket)
        pair = (await self.session.execute(statement)).first()
        if pair is None:
            raise BotExperienceNotFound("Support ticket not found")
        ticket, user = pair
        return await self._ticket_record(ticket, user)

    async def reply_ticket(
        self,
        *,
        ticket_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
        body: str,
    ) -> SupportTicketRecord:
        record = await self.get_ticket(ticket_id, lock=True)
        if record.ticket.status == "closed":
            raise BotExperienceConflict("Support ticket is closed")
        normalized = _clean_text(body, field="Support reply", maximum=3900)
        message = SupportMessage(
            ticket_id=record.ticket.id,
            sender_user_id=actor_user_id,
            sender_telegram_id=actor_telegram_id,
            sender_kind="admin",
            body=normalized,
        )
        self.session.add(message)
        old_status = record.ticket.status
        record.ticket.status = "waiting_user"
        record.ticket.assigned_admin_user_id = actor_user_id
        record.ticket.assigned_at = record.ticket.assigned_at or datetime.now(timezone.utc)
        record.ticket.updated_at = datetime.now(timezone.utc)
        event = self._ticket_event(
            record.ticket,
            event_type="admin_reply",
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            from_status=old_status,
            to_status="waiting_user",
        )
        await self.session.flush()
        self._audit("support.ticket_replied", actor_user_id, actor_telegram_id, record.ticket)
        record.messages.append(message)
        record.events.append(event)
        return record

    async def user_reply_ticket(
        self,
        *,
        ticket_id: int,
        telegram_id: int,
        body: str | None,
        telegram_file_id: str | None,
        file_type: str | None,
    ) -> SupportTicketRecord:
        record = await self.get_ticket(
            ticket_id,
            lock=True,
            user_telegram_id=telegram_id,
        )
        if record.user.status != UserStatus.ACTIVE:
            raise BotExperienceError("User account is inactive")
        if record.ticket.status == "closed":
            raise BotExperienceConflict("Closed tickets must be reopened by an administrator")
        normalized_body = str(body or "").strip()[:3900] or None
        normalized_file_id = str(telegram_file_id or "").strip()[:512] or None
        normalized_file_type = str(file_type or "").strip().lower() or None
        if normalized_file_type not in SUPPORT_FILE_TYPES | {None}:
            raise BotExperienceError("Unsupported support attachment")
        if bool(normalized_file_type) != bool(normalized_file_id):
            raise BotExperienceError("Attachment type and id must be provided together")
        if not normalized_body and not normalized_file_id:
            raise BotExperienceError("Support reply cannot be empty")
        message = SupportMessage(
            ticket_id=record.ticket.id,
            sender_user_id=record.user.id,
            sender_telegram_id=telegram_id,
            sender_kind="user",
            body=normalized_body,
            telegram_file_id=normalized_file_id,
            file_type=normalized_file_type,
        )
        self.session.add(message)
        old_status = record.ticket.status
        record.ticket.status = "in_progress"
        record.ticket.updated_at = datetime.now(timezone.utc)
        event = self._ticket_event(
            record.ticket,
            event_type="user_reply",
            actor_user_id=record.user.id,
            actor_telegram_id=telegram_id,
            from_status=old_status,
            to_status="in_progress",
        )
        await self.session.flush()
        record.messages.append(message)
        record.events.append(event)
        return record

    async def set_ticket_status(
        self,
        *,
        ticket_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
        status: str,
    ) -> SupportTicketRecord:
        if status not in SUPPORT_STATUSES:
            raise BotExperienceError("Unknown support ticket status")
        record = await self.get_ticket(ticket_id, lock=True)
        if record.ticket.status == "closed" and status != "closed":
            raise BotExperienceConflict("Use reopen for a closed support ticket")
        old_status = record.ticket.status
        if old_status == status:
            return record
        record.ticket.status = status
        record.ticket.updated_at = datetime.now(timezone.utc)
        if status == "closed":
            record.ticket.closed_at = record.ticket.updated_at
        event = self._ticket_event(
            record.ticket,
            event_type="status_changed",
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            from_status=old_status,
            to_status=status,
        )
        await self.session.flush()
        record.events.append(event)
        self._audit("support.ticket_status_changed", actor_user_id, actor_telegram_id, record.ticket)
        return record

    async def assign_ticket(
        self,
        *,
        ticket_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
        assignee_telegram_id: int,
    ) -> SupportTicketRecord:
        assignee = await AdminAccessService(self.session).require_permission(
            assignee_telegram_id, "tickets.view"
        )
        if assignee.user_id is None:
            raise BotExperienceNotFound("Assigned administrator is not registered")
        record = await self.get_ticket(ticket_id, lock=True)
        record.ticket.assigned_admin_user_id = assignee.user_id
        record.ticket.assigned_at = datetime.now(timezone.utc)
        if record.ticket.status == "new":
            record.ticket.status = "in_progress"
        event = self._ticket_event(
            record.ticket,
            event_type="assigned",
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            details=str(assignee_telegram_id),
        )
        await self.session.flush()
        record.events.append(event)
        self._audit("support.ticket_assigned", actor_user_id, actor_telegram_id, record.ticket)
        return record

    async def reopen_ticket(
        self,
        *,
        ticket_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
    ) -> SupportTicketRecord:
        # Lock the owner before the ticket, matching creation's lock order.
        owner_id = (await self.session.execute(select(SupportTicket.user_id).where(SupportTicket.id == ticket_id))).scalar_one_or_none()
        if owner_id is None:
            raise BotExperienceNotFound("Support ticket not found")
        await self.session.execute(select(User.id).where(User.id == owner_id).with_for_update())
        record = await self.get_ticket(ticket_id, lock=True)
        open_count = (await self.session.execute(select(func.count(SupportTicket.id)).where(
            SupportTicket.user_id == owner_id, SupportTicket.status.in_(OPEN_SUPPORT_STATUSES)
        ))).scalar_one()
        if open_count >= 3:
            raise BotExperienceConflict("A user can have at most three open support tickets")
        if record.ticket.status != "closed":
            raise BotExperienceConflict("Only a closed ticket can be reopened")
        record.ticket.status = "in_progress"
        record.ticket.closed_at = None
        record.ticket.reopened_count += 1
        record.ticket.updated_at = datetime.now(timezone.utc)
        event = self._ticket_event(
            record.ticket,
            event_type="reopened",
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            from_status="closed",
            to_status="in_progress",
        )
        await self.session.flush()
        record.events.append(event)
        self._audit("support.ticket_reopened", actor_user_id, actor_telegram_id, record.ticket)
        return record

    async def close_ticket(
        self,
        *,
        ticket_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
    ) -> SupportTicketRecord:
        record = await self.get_ticket(ticket_id, lock=True)
        if record.ticket.status == "closed":
            return record
        old_status = record.ticket.status
        record.ticket.status = "closed"
        record.ticket.closed_at = datetime.now(timezone.utc)
        record.ticket.updated_at = record.ticket.closed_at
        record.ticket.assigned_admin_user_id = actor_user_id
        record.ticket.assigned_at = record.ticket.assigned_at or record.ticket.closed_at
        event = self._ticket_event(
            record.ticket,
            event_type="closed",
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            from_status=old_status,
            to_status="closed",
        )
        await self.session.flush()
        self._audit("support.ticket_closed", actor_user_id, actor_telegram_id, record.ticket)
        record.events.append(event)
        return record

    async def set_ticket_admin_message(
        self,
        *,
        ticket_id: int,
        admin_chat_id: int,
        admin_message_id: int,
        admin_message_thread_id: int | None,
    ) -> SupportTicketRecord:
        record = await self.get_ticket(ticket_id, lock=True)
        record.ticket.admin_chat_id = admin_chat_id
        record.ticket.admin_message_id = admin_message_id
        record.ticket.admin_message_thread_id = admin_message_thread_id
        await self.session.flush()
        return record

    async def support_recipient_telegram_ids(self) -> list[int]:
        superadmin_result = await self.session.execute(
            select(User.telegram_id)
            .join(AdminAccount, AdminAccount.user_id == User.id)
            .where(AdminAccount.is_active.is_(True), AdminAccount.is_superadmin.is_(True))
        )
        role_result = await self.session.execute(
            select(distinct(User.telegram_id))
            .join(AdminAccount, AdminAccount.user_id == User.id)
            .join(AdminRoleAssignment, AdminRoleAssignment.admin_account_id == AdminAccount.id)
            .join(AdminRole, AdminRole.id == AdminRoleAssignment.role_id)
            .join(AdminRolePermission, AdminRolePermission.role_id == AdminRole.id)
            .join(AdminPermission, AdminPermission.id == AdminRolePermission.permission_id)
            .where(
                AdminAccount.is_active.is_(True),
                AdminRole.is_active.is_(True),
                AdminPermission.code.in_(("tickets.view", "tickets.reply")),
            )
        )
        return sorted(set(superadmin_result.scalars()) | set(role_result.scalars()))

    async def _ticket_messages(self, ticket_id: int) -> list[SupportMessage]:
        result = await self.session.execute(
            select(SupportMessage)
            .where(SupportMessage.ticket_id == ticket_id)
            .order_by(SupportMessage.created_at, SupportMessage.id)
        )
        return list(result.scalars())

    async def _ticket_events(self, ticket_id: int) -> list[SupportTicketEvent]:
        result = await self.session.execute(
            select(SupportTicketEvent)
            .where(SupportTicketEvent.ticket_id == ticket_id)
            .order_by(SupportTicketEvent.created_at, SupportTicketEvent.id)
        )
        return list(result.scalars())

    async def _ticket_plan_name(self, user_id: int, language: str = "fa") -> str | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(Plan.name, Plan.name_en, Plan.id)
            .join(Subscription, Subscription.plan_id == Plan.id)
            .where(
                Subscription.user_id == user_id,
                Subscription.status.in_(
                    (SubscriptionStatus.ACTIVE, SubscriptionStatus.SCHEDULED)
                ),
                Subscription.started_at <= now,
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.desc(), Subscription.id.desc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None
        if language == "en":
            return row[1] if row[1] and not re.search(r"[\u0600-\u06ff]", row[1]) else f"Plan {row[2]}"
        return row[0]

    async def _ticket_record(
        self,
        ticket: SupportTicket,
        user: User,
    ) -> SupportTicketRecord:
        return SupportTicketRecord(
            ticket=ticket,
            user=user,
            messages=await self._ticket_messages(ticket.id),
            events=await self._ticket_events(ticket.id),
            plan_name=await self._ticket_plan_name(user.id, effective_language(preferred_language=user.preferred_language, telegram_language_code=user.language_code)),
        )

    def _ticket_event(
        self,
        ticket: SupportTicket,
        *,
        event_type: str,
        actor_user_id: int | None,
        actor_telegram_id: int | None,
        from_status: str | None = None,
        to_status: str | None = None,
        details: str | None = None,
    ) -> SupportTicketEvent:
        event = SupportTicketEvent(
            ticket_id=ticket.id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            details=details,
        )
        self.session.add(event)
        return event

    def _audit(
        self,
        action: str,
        actor_user_id: int,
        actor_telegram_id: int,
        target: HomeButton | RequiredChannel | SupportTicket,
    ) -> None:
        AuditService(self.session).record(
            action=action,
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            target_type=target.__tablename__,
            target_id=target.id,
        )
