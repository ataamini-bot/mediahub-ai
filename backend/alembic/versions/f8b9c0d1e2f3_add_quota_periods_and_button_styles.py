"""Add weekly quota periods and configurable built-in button colors.

Revision ID: f8b9c0d1e2f3
Revises: f7a8b9c0d1e2
"""
from alembic import op
import sqlalchemy as sa


revision = "f8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "plans",
        sa.Column("download_limit_period", sa.String(16), server_default="daily", nullable=False),
    )
    op.create_check_constraint(
        "ck_plans_download_limit_period",
        "plans",
        "download_limit_period IN ('daily', 'weekly')",
    )
    op.add_column(
        "subscriptions",
        sa.Column("download_limit_period", sa.String(16), server_default="daily", nullable=False),
    )
    op.create_check_constraint(
        "ck_subscriptions_download_limit_period",
        "subscriptions",
        "download_limit_period IN ('daily', 'weekly')",
    )

    # The existing Free numeric limit is preserved; only its consumption
    # window changes. Existing snapshots follow the plan period as well.
    op.execute(sa.text("UPDATE plans SET download_limit_period = 'weekly' WHERE slug = 'free'"))
    op.execute(sa.text("""
        UPDATE subscriptions AS s
        SET download_limit_period = p.download_limit_period
        FROM plans AS p
        WHERE p.id = s.plan_id
    """))

    # Empty maps allow the runtime's safe defaults to remain active while
    # giving the admin panel a versioned setting to edit immediately.
    op.execute(sa.text("""
        INSERT INTO application_settings
            (key, category, value_json, is_sensitive, version, description)
        VALUES
            ('bot.button_styles.fa', 'bot_buttons', '{}'::json, false, 1,
             'Telegram styles for Persian built-in buttons'),
            ('bot.button_styles.en', 'bot_buttons', '{}'::json, false, 1,
             'Telegram styles for English built-in buttons')
        ON CONFLICT (key) DO NOTHING
    """))


def downgrade():
    op.execute(sa.text("DELETE FROM application_settings WHERE key IN ('bot.button_styles.fa', 'bot.button_styles.en')"))
    op.drop_constraint("ck_subscriptions_download_limit_period", "subscriptions", type_="check")
    op.drop_column("subscriptions", "download_limit_period")
    op.drop_constraint("ck_plans_download_limit_period", "plans", type_="check")
    op.drop_column("plans", "download_limit_period")
