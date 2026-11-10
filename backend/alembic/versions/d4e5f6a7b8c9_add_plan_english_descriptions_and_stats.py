"""add English plan descriptions and statistics permission

Revision ID: d4e5f6a7b8c9
Revises: c7e4d2a9f610
Create Date: 2026-09-08 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c7e4d2a9f610"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("description_en", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            """
            INSERT INTO admin_permissions (code, description)
            VALUES ('statistics.view', 'View bot statistics and growth metrics')
            ON CONFLICT (code) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO admin_role_permissions (role_id, permission_id)
            SELECT r.id, p.id
            FROM admin_roles r
            CROSS JOIN admin_permissions p
            WHERE r.code = 'technical_monitoring'
              AND p.code = 'statistics.view'
            ON CONFLICT DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM admin_role_permissions rp
            USING admin_permissions p
            WHERE rp.permission_id = p.id AND p.code = 'statistics.view'
            """
        )
    )
    op.execute(sa.text("DELETE FROM admin_permissions WHERE code = 'statistics.view'"))
    op.drop_column("plans", "description_en")
