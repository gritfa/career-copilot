"""initial baseline (no business tables yet)

Revision ID: a1f0c3d9e5b7
Revises:
Create Date: 2026-07-28

空基线迁移：只建立迁移链起点，业务表在后续阶段各自的迁移中创建。
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "a1f0c3d9e5b7"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
