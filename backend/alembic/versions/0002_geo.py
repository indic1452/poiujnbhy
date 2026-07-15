"""Гео-поля событий и типы событий (карта/аналитика).

Revision ID: 0002_geo
Revises: 0001_initial
Create Date: 2026-07-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_geo"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clusters", sa.Column("lat", sa.Float(), nullable=True))
    op.add_column("clusters", sa.Column("lon", sa.Float(), nullable=True))
    op.add_column("clusters", sa.Column("place_name", sa.String(200), nullable=True))
    op.add_column("clusters", sa.Column("admin1", sa.String(120), nullable=True))
    op.add_column("clusters", sa.Column("admin2", sa.String(120), nullable=True))
    op.add_column("clusters", sa.Column("country", sa.String(8), nullable=True))
    op.add_column("clusters", sa.Column("event_type", sa.String(40), nullable=True))
    op.add_column("clusters", sa.Column("geo_source", sa.String(30), nullable=True))
    op.add_column(
        "clusters",
        sa.Column("geo_confidence", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "clusters",
        sa.Column(
            "geo_needs_review", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("clusters", sa.Column("locations", JSONB(), nullable=True))

    op.add_column("items", sa.Column("event_type", sa.String(40), nullable=True))
    op.add_column("items", sa.Column("locations", JSONB(), nullable=True))


def downgrade() -> None:
    for col in ("locations", "event_type"):
        op.drop_column("items", col)
    for col in (
        "locations",
        "geo_needs_review",
        "geo_confidence",
        "geo_source",
        "event_type",
        "country",
        "admin2",
        "admin1",
        "place_name",
        "lon",
        "lat",
    ):
        op.drop_column("clusters", col)
