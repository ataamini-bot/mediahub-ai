"""add subscription quota stacking

Revision ID: 9b4e2d6f1a30
Revises: 8c3d4e5f6a71
Create Date: 2026-09-05 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9b4e2d6f1a30"
down_revision: Union[str, None] = "8c3d4e5f6a71"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("daily_download_limit", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_subscriptions_daily_download_limit_positive",
        "subscriptions",
        "daily_download_limit IS NULL OR daily_download_limit > 0",
    )

    # Existing renewals already point at the subscription they extended.
    # Reconstruct their cumulative quota from immutable payment snapshots so
    # customers who renewed before this migration receive every purchased
    # quota unit.  Subscriptions with no payment history fall back to the
    # current plan limit (for example manually-created legacy subscriptions).
    op.execute(
        """
        UPDATE subscriptions AS subscription
        SET daily_download_limit = CASE
            WHEN plan.daily_download_limit IS NULL THEN NULL
            ELSE COALESCE(
                (
                    SELECT SUM(
                        COALESCE(
                            NULLIF(
                                payment.plan_limits_snapshot
                                    ->> 'daily_download_limit',
                                ''
                            )::integer,
                            plan.daily_download_limit
                        )
                    )
                    FROM payments AS payment
                    WHERE payment.subscription_id = subscription.id
                      AND payment.status::text = 'APPROVED'
                ),
                plan.daily_download_limit
            )
        END
        FROM plans AS plan
        WHERE plan.id = subscription.plan_id
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_subscriptions_daily_download_limit_positive",
        "subscriptions",
        type_="check",
    )
    op.drop_column("subscriptions", "daily_download_limit")
