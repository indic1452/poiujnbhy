"""Разделяемое runtime-состояние (между API и планировщиком)."""
from __future__ import annotations

import asyncio
from datetime import datetime

ingest_lock = asyncio.Lock()
last_ingest: datetime | None = None
last_stats: dict | None = None
