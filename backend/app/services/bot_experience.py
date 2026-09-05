from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import distinct, select
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
)
from app.models.user import User
from app.services.application_settings import ApplicationSettingsService
from app.services.audit import AuditService


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
SUPPORT_CATEGORIES = {"financial", "technical", "account", "general"}
SUPPORT_FILE_TYPES = {"photo", "document", "video", "voice"}


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
            await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        ).scalar_one_or_none()
        if user is None:
            raise BotExperienceNotFound("Telegram user is not registered")
        ticket = SupportTicket(user_id=user.id, category=normalized_category, status="open")
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
        await self.session.flush()
        AuditService(self.session).record(
            action="support.ticket_created",
            actor_user_id=user.id,
            actor_telegram_id=telegram_id,
            target_type="support_ticket",
            target_id=ticket.id,
            details={"category": normalized_category, "file_type": normalized_file_type},
        )
        return SupportTicketRecord(ticket=ticket, user=user, messages=[message])

    async def list_tickets(
        self,
        *,
        status: str | None = None,
        limit: int = 30,
    ) -> list[SupportTicketRecord]:
        statement = (
            select(SupportTicket, User)
            .join(User, User.id == SupportTicket.user_id)
            .order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc())
            .limit(max(1, min(limit, 100)))
        )
        if status in {"open", "answered", "closed"}:
            statement = statement.where(SupportTicket.status == status)
        pairs = list((await self.session.execute(statement)).all())
        records: list[SupportTicketRecord] = []
        for ticket, user in pairs:
            messages = await self._ticket_messages(ticket.id)
            records.append(SupportTicketRecord(ticket=ticket, user=user, messages=messages))
        return records

    async def get_ticket(self, ticket_id: int, *, lock: bool = False) -> SupportTicketRecord:
        statement = (
            select(SupportTicket, User)
            .join(User, User.id == SupportTicket.user_id)
            .where(SupportTicket.id == ticket_id)
        )
        if lock:
            statement = statement.with_for_update()
        pair = (await self.session.execute(statement)).first()
        if pair is None:
            raise BotExperienceNotFound("Support ticket not found")
        ticket, user = pair
        return SupportTicketRecord(
            ticket=ticket,
            user=user,
            messages=await self._ticket_messages(ticket.id),
        )

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
        record.ticket.status = "answered"
        record.ticket.assigned_admin_user_id = actor_user_id
        record.ticket.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        self._audit("support.ticket_replied", actor_user_id, actor_telegram_id, record.ticket)
        record.messages.append(message)
        return record

    async def close_ticket(
        self,
        *,
        ticket_id: int,
        actor_user_id: int,
        actor_telegram_id: int,
    ) -> SupportTicketRecord:
        record = await self.get_ticket(ticket_id, lock=True)
        record.ticket.status = "closed"
        record.ticket.closed_at = datetime.now(timezone.utc)
        record.ticket.updated_at = record.ticket.closed_at
        record.ticket.assigned_admin_user_id = actor_user_id
        await self.session.flush()
        self._audit("support.ticket_closed", actor_user_id, actor_telegram_id, record.ticket)
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
