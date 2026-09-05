"""add bot experience management

Revision ID: 7a2c9e1f4b60
Revises: 5d1a9c7e2f40
Create Date: 2026-09-03 10:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a2c9e1f4b60"
down_revision: Union[str, None] = "5d1a9c7e2f40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONTENT_FA = {
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
}

CONTENT_EN = {
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
    "forced_join": (
        "Join the channels below, then tap Check membership to use downloads."
    ),
    "membership_verified": "✅ Membership verified. You can send the link again now.",
}

BUTTONS_FA = {
    "buy": "💎 خرید اشتراک",
    "subscription": "👤 وضعیت اشتراک من",
    "language": "🌐 تغییر زبان | Language",
    "support": "🛟 پشتیبانی",
    "tutorial": "📘 آموزش استفاده",
    "faq": "❓ سوالات متداول",
    "admin": "⚙️ پنل مدیریت",
    "check_membership": "✅ بررسی عضویت",
    "back_home": "🏠 منوی اصلی",
}

BUTTONS_EN = {
    "buy": "💎 Buy subscription",
    "subscription": "👤 My subscription",
    "language": "🌐 Language | تغییر زبان",
    "support": "🛟 Support",
    "tutorial": "📘 How to use",
    "faq": "❓ FAQ",
    "admin": "⚙️ Admin panel",
    "check_membership": "✅ Check membership",
    "back_home": "🏠 Main menu",
}


def upgrade() -> None:
    op.create_table(
        "home_buttons",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("label_fa", sa.String(length=64), nullable=False),
        sa.Column("label_en", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=24), nullable=False),
        sa.Column("action_value", sa.Text(), nullable=True),
        sa.Column("style", sa.String(length=16), server_default="default", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="100", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "action_type IN ('url', 'message', 'buy', 'subscription', 'support', 'tutorial', 'faq')",
            name="ck_home_buttons_action_type",
        ),
        sa.CheckConstraint(
            "style IN ('default', 'primary', 'success', 'danger')",
            name="ck_home_buttons_style",
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_home_buttons_sort_order_nonnegative"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_home_buttons_active_order",
        "home_buttons",
        ["is_active", "sort_order", "id"],
    )

    op.create_table(
        "required_channels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("invite_url", sa.String(length=500), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("sort_order >= 0", name="ck_required_channels_sort_order_nonnegative"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id"),
    )
    op.create_index(
        "ix_required_channels_active_order",
        "required_channels",
        ["is_active", "sort_order", "id"],
    )

    op.create_table(
        "support_tickets",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("assigned_admin_user_id", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "category IN ('financial', 'technical', 'account', 'general')",
            name="ck_support_tickets_category",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'answered', 'closed')",
            name="ck_support_tickets_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_admin_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_tickets_user_id", "support_tickets", ["user_id"])
    op.create_index(
        "ix_support_tickets_status_updated",
        "support_tickets",
        ["status", "updated_at", "id"],
    )

    op.create_table(
        "support_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_user_id", sa.Integer(), nullable=True),
        sa.Column("sender_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_kind", sa.String(length=12), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("telegram_file_id", sa.String(length=512), nullable=True),
        sa.Column("file_type", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "sender_kind IN ('user', 'admin')",
            name="ck_support_messages_sender_kind",
        ),
        sa.CheckConstraint(
            "file_type IS NULL OR file_type IN ('photo', 'document', 'video', 'voice')",
            name="ck_support_messages_file_type",
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_messages_ticket_id", "support_messages", ["ticket_id"])
    op.create_index(
        "ix_support_messages_ticket_created",
        "support_messages",
        ["ticket_id", "created_at", "id"],
    )

    connection = op.get_bind()
    rows = (
        ("bot.content.fa", "bot_content", CONTENT_FA, "Persian user-facing content"),
        ("bot.content.en", "bot_content", CONTENT_EN, "English user-facing content"),
        ("bot.buttons.fa", "bot_buttons", BUTTONS_FA, "Persian user-facing button labels"),
        ("bot.buttons.en", "bot_buttons", BUTTONS_EN, "English user-facing button labels"),
    )
    statement = sa.text(
        """
        INSERT INTO application_settings (
            key, category, value_json, is_sensitive, description, version
        )
        VALUES (:key, :category, CAST(:value AS json), FALSE, :description, 1)
        ON CONFLICT (key) DO NOTHING
        """
    )
    for key, category, value, description in rows:
        connection.execute(
            statement,
            {
                "key": key,
                "category": category,
                "value": json.dumps(value, ensure_ascii=False),
                "description": description,
            },
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM application_settings WHERE key IN "
            "('bot.content.fa', 'bot.content.en', 'bot.buttons.fa', 'bot.buttons.en')"
        )
    )
    op.drop_index("ix_support_messages_ticket_created", table_name="support_messages")
    op.drop_index("ix_support_messages_ticket_id", table_name="support_messages")
    op.drop_table("support_messages")
    op.drop_index("ix_support_tickets_status_updated", table_name="support_tickets")
    op.drop_index("ix_support_tickets_user_id", table_name="support_tickets")
    op.drop_table("support_tickets")
    op.drop_index("ix_required_channels_active_order", table_name="required_channels")
    op.drop_table("required_channels")
    op.drop_index("ix_home_buttons_active_order", table_name="home_buttons")
    op.drop_table("home_buttons")
