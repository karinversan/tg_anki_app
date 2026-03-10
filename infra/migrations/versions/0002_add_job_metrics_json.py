"""add metrics_json to generation_jobs

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_jobs", sa.Column("metrics_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("generation_jobs", "metrics_json")
