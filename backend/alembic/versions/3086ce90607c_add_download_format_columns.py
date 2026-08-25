"""bridge duplicate download format migration

Revision ID: 3086ce90607c
Revises: 8ea89b7b0047
Create Date: 2026-08-09 14:26:35.818983

This revision previously duplicated the format_id, quality and
media_type columns already introduced by revision 8ea89b7b0047.

The production database is currently stamped at this revision,
so this migration is intentionally kept as a no-op bridge.
"""

from typing import Sequence, Union


revision: str = "3086ce90607c"
down_revision: Union[str, None] = "8ea89b7b0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
