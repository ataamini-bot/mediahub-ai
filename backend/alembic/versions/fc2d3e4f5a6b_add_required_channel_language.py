"""Scope required channels to the selected bot language.

Revision ID: fc2d3e4f5a6b
Revises: fb1c2d3e4f5a
"""
from alembic import op
import sqlalchemy as sa


revision = "fc2d3e4f5a6b"
down_revision = "fb1c2d3e4f5a"
branch_labels = None
depends_on = None


def upgrade():
    # Existing channels keep applying to both languages until the admin chooses.
    op.add_column("required_channels", sa.Column(
        "language", sa.String(3), server_default="all", nullable=False,
    ))
    op.create_check_constraint(
        "ck_required_channels_language", "required_channels",
        "language IN ('all', 'fa', 'en')",
    )


def downgrade():
    op.drop_constraint("ck_required_channels_language", "required_channels", type_="check")
    op.drop_column("required_channels", "language")
