from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.db.models import Topic, FileRecord
from app.services.storage import delete_file


async def remove_topic_assets(session: AsyncSession, topic: Topic) -> None:
    # DB rows are cascade-deleted; remove stored blobs and exports.
    result = await session.execute(select(FileRecord).where(FileRecord.topic_id == topic.id))
    for file_record in result.scalars().all():
        delete_file(file_record.storage_path)
    exports_dir = Path(settings.storage_path) / "exports" / str(topic.id)
    if exports_dir.exists():
        for path in exports_dir.glob("*"):
            delete_file(str(path))
