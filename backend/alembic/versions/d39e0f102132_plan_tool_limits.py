"""Music quotas and editable copy; retire portrait setup without deleting history.

Revision ID: d39e0f102132
Revises: c28d9e0f1021
"""
from alembic import op
import sqlalchemy as sa

revision = "d39e0f102132"
down_revision = "c28d9e0f1021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plans", sa.Column("tool_limits", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    for language in ("fa", "en"):
        op.get_bind().execute(sa.text("""INSERT INTO application_settings
            (key, category, value_json, is_sensitive, description, version)
            VALUES (:key, 'bot_content', '{}'::json, FALSE, :key, 1)
            ON CONFLICT (key) DO NOTHING"""), {"key": f"bot.tool_texts.{language}"})
    # Retain operational history, but revoke pending work and provider credentials.
    op.execute("DELETE FROM application_settings WHERE key LIKE 'tools.portrait.%'")
    op.execute("UPDATE media_tool_requests SET status='cancelled', error_code='tool_disabled' WHERE kind='portrait' AND status IN ('pending','processing')")
    op.execute("""UPDATE application_settings SET value_json=(value_json::jsonb - 'portrait')::json,
        version=version+1 WHERE key IN ('bot.buttons.fa','bot.buttons.en','bot.button_styles.fa','bot.button_styles.en')""")
    op.execute("""UPDATE application_settings SET value_json=jsonb_set(value_json::jsonb, '{order}',
        COALESCE((SELECT jsonb_agg(item) FROM jsonb_array_elements(value_json::jsonb->'order') item WHERE item <> '"portrait"'::jsonb), '[]'::jsonb))::json,
        version=version+1 WHERE key IN ('bot.home_layout.fa','bot.home_layout.en') AND jsonb_typeof(value_json::jsonb->'order')='array'""")


def downgrade():
    op.drop_column("plans", "tool_limits")
    op.execute("DELETE FROM application_settings WHERE key IN ('bot.tool_texts.fa','bot.tool_texts.en')")
