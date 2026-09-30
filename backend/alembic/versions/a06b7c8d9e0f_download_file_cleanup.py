"""Track removal of temporary files while preserving activity records."""
from alembic import op
import sqlalchemy as sa

revision = "a06b7c8d9e0f"
down_revision = "ff5a6b7c8d9e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("download_jobs", sa.Column("files_removed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_download_jobs_files_removed_at", "download_jobs", ["files_removed_at"])


def downgrade():
    op.drop_index("ix_download_jobs_files_removed_at", table_name="download_jobs")
    op.drop_column("download_jobs", "files_removed_at")
