"""Two-currency internal credit with append-only ledger and notifications."""
from alembic import op
import sqlalchemy as sa

revision = "fe4f5a6b7c8d"
down_revision = "fd3e4f5a6b7c"
branch_labels = None
depends_on = None


def upgrade():
    # The old, unused Wallet model has no currency. Never silently convert live money.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM wallets WHERE balance <> 0")):
        raise RuntimeError("Legacy wallet has a nonzero balance. Verify its currency before migrating; no balance was changed.")
    op.alter_column("payments", "amount", type_=sa.Numeric(18, 4), existing_type=sa.Numeric(12, 4))
    op.create_table("credit_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("currency", sa.String(4), nullable=False),
        sa.Column("balance", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "currency", name="uq_credit_account_currency"),
        sa.CheckConstraint("currency IN ('IRT', 'USDT')", name="ck_credit_currency"),
        sa.CheckConstraint("balance >= 0", name="ck_credit_nonnegative"),
        sa.CheckConstraint("currency != 'IRT' OR balance = trunc(balance)", name="ck_credit_whole_toman"),
    )
    op.create_index("ix_credit_accounts_user_id", "credit_accounts", ["user_id"])
    op.create_table("credit_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("credit_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("delta", sa.Numeric(18, 4), nullable=False),
        sa.Column("balance_after", sa.Numeric(18, 4), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("reverses_entry_id", sa.Uuid(), sa.ForeignKey("credit_entries.id", ondelete="RESTRICT"), unique=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="RESTRICT"), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("delta != 0", name="ck_credit_entry_nonzero"),
        sa.CheckConstraint("balance_after >= 0", name="ck_credit_entry_balance"),
        sa.CheckConstraint("kind IN ('adjustment', 'reversal', 'purchase')", name="ck_credit_entry_kind"),
    )
    op.create_index("ix_credit_entries_account_id", "credit_entries", ["account_id"])
    op.execute("""CREATE FUNCTION mediahub_credit_append_only() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Credit ledger is append-only; use a reversal entry'; END;
        $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER credit_entries_append_only BEFORE UPDATE OR DELETE ON credit_entries
                  FOR EACH ROW EXECUTE FUNCTION mediahub_credit_append_only()""")
    op.create_table("credit_notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Uuid(), sa.ForeignKey("credit_entries.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("claim_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("entry_id", "kind", name="uq_credit_notice"),
    )
    op.create_index("ix_credit_notices_entry_id", "credit_notices", ["entry_id"])
    op.create_index("ix_credit_notices_status", "credit_notices", ["status"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM credit_entries")):
        raise RuntimeError("Cannot remove an internal-credit ledger containing financial history")
    op.drop_table("credit_notices")
    op.drop_table("credit_entries")
    op.execute("DROP FUNCTION mediahub_credit_append_only()")
    op.drop_table("credit_accounts")
    op.alter_column("payments", "amount", type_=sa.Numeric(12, 4), existing_type=sa.Numeric(18, 4))
