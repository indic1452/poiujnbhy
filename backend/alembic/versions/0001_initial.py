"""Начальная схема (sources, clusters, items, media).

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op

from app.db import Base
from app import models  # noqa: F401  (регистрация моделей)

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
