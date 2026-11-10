"""Add configurable main-menu order and row-width settings.

Revision ID: fa0b1c2d3e4f
Revises: f9c0d1e2f3a4
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fa0b1c2d3e4f"
down_revision: Union[str, None] = "f9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DEFAULT_LAYOUT = (
    '{"columns":2,"order":['
    '"buy","subscription","support","language",'
    '"convert","tutorial","faq","admin"'
    ']}'
)


def upgrade() -> None:
    # Seed versioned rows so the Bot panel can use optimistic locking from
    # its first edit. Existing installations retain the familiar two-column
    # menu until an administrator changes it.
    op.execute(
        sa.text(
            """
            INSERT INTO application_settings
                (key, category, value_json, is_sensitive, version, description)
            VALUES
                ('bot.home_layout.fa', 'bot_buttons', CAST(:layout AS json), false, 1,
                 'Order and row width for Persian main-menu buttons'),
                ('bot.home_layout.en', 'bot_buttons', CAST(:layout AS json), false, 1,
                 'Order and row width for English main-menu buttons')
            ON CONFLICT (key) DO NOTHING
            """
        ).bindparams(layout=_DEFAULT_LAYOUT)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM application_settings "
            "WHERE key IN ('bot.home_layout.fa', 'bot.home_layout.en')"
        )
    )
