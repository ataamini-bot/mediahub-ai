"""complete payments, tickets, reporting and operations foundation

Revision ID: e6f7a8b9c0d1
Revises: d4e5f6a7b8c9
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


SETTING_ROWS = (
    ("notifications.enabled", "notifications", None, "Enable Telegram operations notifications"),
    ("notifications.chat_id", "notifications", None, "Admin supergroup chat id"),
    ("notifications.topic.monitoring", "notifications", None, "Monitoring Topic id"),
    ("notifications.topic.payments", "notifications", None, "Payments Topic id"),
    ("notifications.topic.backups", "notifications", None, "Backups Topic id"),
    ("notifications.topic.system", "notifications", None, "System Topic id"),
    ("notifications.topic.support", "notifications", None, "Support Topic id"),
    ("monitor.interval_seconds", "monitoring", 30, "Health monitor interval"),
    ("monitor.bot_heartbeat_seconds", "monitoring", 90, "Maximum bot heartbeat age"),
    ("monitor.disk_percent", "monitoring", 85, "Disk usage warning threshold"),
    ("monitor.ram_percent", "monitoring", 90, "RAM usage warning threshold"),
    ("monitor.cpu_percent", "monitoring", 90, "CPU usage warning threshold"),
    ("monitor.celery_queue_size", "monitoring", 100, "Celery queue warning threshold"),
    ("monitor.download_error_percent", "monitoring", 25, "Download error-rate warning threshold"),
    ("monitor.download_error_window_minutes", "monitoring", 15, "Download error-rate window"),
)


def upgrade() -> None:
    op.alter_column("payments", "receipt_file_id", existing_type=sa.String(length=512), nullable=True)
    op.alter_column("payments", "receipt_file_type", existing_type=sa.String(length=32), nullable=True)
    op.add_column("payments", sa.Column("txid", sa.String(length=255), nullable=True))
    op.add_column("payments", sa.Column("txid_normalized", sa.String(length=255), nullable=True))
    op.add_column("payments", sa.Column("usdt_network_code", sa.String(length=32), nullable=True))
    op.add_column("payments", sa.Column("subscription_change_type", sa.String(length=24), nullable=True))
    op.create_index(
        "uq_payments_usdt_network_txid",
        "payments",
        ["usdt_network_code", "txid_normalized"],
        unique=True,
        postgresql_where=sa.text("txid_normalized IS NOT NULL"),
    )
    op.create_index("ix_payments_txid_normalized", "payments", ["txid_normalized"])

    # PostgreSQL enums require explicit extension before SQLAlchemy can write
    # the new scheduled state.
    op.execute("ALTER TYPE subscriptionstatus ADD VALUE IF NOT EXISTS 'SCHEDULED'")

    op.drop_constraint("ck_support_tickets_category", "support_tickets", type_="check")
    op.drop_constraint("ck_support_tickets_status", "support_tickets", type_="check")
    op.execute(
        """
        UPDATE support_tickets
        SET category = CASE category
            WHEN 'financial' THEN 'payment'
            WHEN 'technical' THEN 'download'
            WHEN 'general' THEN 'other'
            ELSE category
        END,
        status = CASE status
            WHEN 'open' THEN 'new'
            ELSE status
        END
        """
    )
    op.alter_column("support_tickets", "status", existing_type=sa.String(length=16), type_=sa.String(length=24), server_default="new", nullable=False)
    op.add_column("support_tickets", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("support_tickets", sa.Column("reopened_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("support_tickets", sa.Column("admin_chat_id", sa.BigInteger(), nullable=True))
    op.add_column("support_tickets", sa.Column("admin_message_id", sa.BigInteger(), nullable=True))
    op.add_column("support_tickets", sa.Column("admin_message_thread_id", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "ck_support_tickets_category",
        "support_tickets",
        "category IN ('download', 'payment', 'subscription', 'account', 'other')",
    )
    op.create_check_constraint(
        "ck_support_tickets_status",
        "support_tickets",
        "status IN ('new', 'in_progress', 'waiting_user', 'answered', 'closed')",
    )
    op.create_table(
        "support_ticket_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_ticket_events_ticket_id", "support_ticket_events", ["ticket_id"])
    op.create_index("ix_support_ticket_events_ticket_created", "support_ticket_events", ["ticket_id", "created_at", "id"])

    connection = op.get_bind()
    for key, category, value, description in SETTING_ROWS:
        connection.execute(
            sa.text(
                """
                INSERT INTO application_settings
                    (key, category, value_json, is_sensitive, description, version)
                VALUES (:key, :category, CAST(:value AS json), false, :description, 1)
                ON CONFLICT (key) DO NOTHING
                """
            ),
            {"key": key, "category": category, "value": "null" if value is None else str(value).lower(), "description": description},
        )


def downgrade() -> None:
    connection = op.get_bind()
    incompatible = connection.execute(sa.text("""
        SELECT EXISTS (SELECT 1 FROM payments WHERE txid IS NOT NULL
                       OR receipt_file_id IS NULL OR subscription_change_type IS NOT NULL)
            OR EXISTS (SELECT 1 FROM support_ticket_events)
            OR EXISTS (SELECT 1 FROM subscriptions WHERE status::text = 'SCHEDULED')
    """)).scalar_one()
    if incompatible:
        raise RuntimeError("Downgrade would discard payment or ticket history. Use a forward recovery migration; database data was not changed.")
    connection.execute(
        sa.text("DELETE FROM application_settings WHERE key = ANY(:keys)"),
        {"keys": [row[0] for row in SETTING_ROWS]},
    )
    op.drop_index("ix_support_ticket_events_ticket_created", table_name="support_ticket_events")
    op.drop_index("ix_support_ticket_events_ticket_id", table_name="support_ticket_events")
    op.drop_table("support_ticket_events")
    op.drop_constraint("ck_support_tickets_status", "support_tickets", type_="check")
    op.drop_constraint("ck_support_tickets_category", "support_tickets", type_="check")
    op.execute("UPDATE support_tickets SET category = CASE category WHEN 'payment' THEN 'financial' WHEN 'download' THEN 'technical' WHEN 'other' THEN 'general' WHEN 'subscription' THEN 'account' ELSE category END, status = CASE WHEN status = 'new' THEN 'open' WHEN status IN ('in_progress', 'waiting_user') THEN 'answered' ELSE status END")
    op.drop_column("support_tickets", "admin_message_thread_id")
    op.drop_column("support_tickets", "admin_message_id")
    op.drop_column("support_tickets", "admin_chat_id")
    op.drop_column("support_tickets", "reopened_count")
    op.drop_column("support_tickets", "assigned_at")
    op.alter_column("support_tickets", "status", existing_type=sa.String(length=24), type_=sa.String(length=16), server_default="open", nullable=False)
    op.create_check_constraint("ck_support_tickets_category", "support_tickets", "category IN ('financial', 'technical', 'account', 'general')")
    op.create_check_constraint("ck_support_tickets_status", "support_tickets", "status IN ('open', 'answered', 'closed')")
    op.drop_index("ix_payments_txid_normalized", table_name="payments")
    op.drop_index("uq_payments_usdt_network_txid", table_name="payments")
    op.drop_column("payments", "subscription_change_type")
    op.drop_column("payments", "usdt_network_code")
    op.drop_column("payments", "txid_normalized")
    op.drop_column("payments", "txid")
    op.alter_column("payments", "receipt_file_type", existing_type=sa.String(length=32), nullable=False)
    op.alter_column("payments", "receipt_file_id", existing_type=sa.String(length=512), nullable=False)
