"""Store the requested final audio/video output format.

Revision ID: f9c0d1e2f3a4
Revises: f8b9c0d1e2f3
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f9c0d1e2f3a4"
down_revision: Union[str, None] = "f8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SUPPORTED_FORMATS = "'mp3','m4a','wav','aac','flac','ogg','opus','mp4','mkv','avi','mov','webm'"


def upgrade() -> None:
    # ``convert`` is also a valid built-in action in the admin-managed
    # home-button table.  Replace the original check constraint so an admin
    # can safely expose or customize that action after this migration.
    op.drop_constraint(
        "ck_home_buttons_action_type",
        "home_buttons",
        type_="check",
    )
    op.create_check_constraint(
        "ck_home_buttons_action_type",
        "home_buttons",
        "action_type IN ('url', 'message', 'buy', 'subscription', 'support', 'convert', 'tutorial', 'faq')",
    )
    op.add_column(
        "download_jobs",
        sa.Column("output_format", sa.String(length=16), nullable=True),
    )
    op.create_check_constraint(
        "ck_download_jobs_output_format",
        "download_jobs",
        f"output_format IS NULL OR output_format IN ({_SUPPORTED_FORMATS})",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_download_jobs_output_format",
        "download_jobs",
        type_="check",
    )
    op.drop_column("download_jobs", "output_format")
    op.drop_constraint(
        "ck_home_buttons_action_type",
        "home_buttons",
        type_="check",
    )
    op.create_check_constraint(
        "ck_home_buttons_action_type",
        "home_buttons",
        "action_type IN ('url', 'message', 'buy', 'subscription', 'support', 'tutorial', 'faq')",
    )
