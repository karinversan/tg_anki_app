from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_file_for_user, get_topic_for_user
from app.core.config import settings
from app.db.models import FileRecord, Topic, User
from app.db.session import get_session
from app.schemas.file import FileOut
from app.services.cache import get_redis
from app.services.clamav import scan_bytes
from app.services.rate_limit import check_rate_limit
from app.services.storage import delete_file, sha256_hex, write_encrypted_file
from app.services.validation import validate_file

router = APIRouter(prefix="/topics/{topic_id}/files", tags=["files"])


@router.get("/", response_model=list[FileOut])
async def list_files(
    topic_id: UUID,
    topic: Topic = Depends(get_topic_for_user),
    session: AsyncSession = Depends(get_session),
) -> list[FileOut]:
    stmt = (
        select(FileRecord)
        .where(FileRecord.topic_id == topic.id)
        .where(FileRecord.deleted_at.is_(None))
        .order_by(FileRecord.created_at.desc())
    )
    result = await session.execute(stmt)
    return [FileOut.model_validate(item) for item in result.scalars().all()]


@router.post("/")
async def upload_file(
    topic_id: UUID,
    file: UploadFile = File(...),
    topic: Topic = Depends(get_topic_for_user),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FileOut:
    redis = get_redis()
    try:
        await check_rate_limit(
            redis,
            f"rate:upload:{user.id}",
            settings.rate_limit_uploads_per_minute,
            60,
        )
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    count_result = await session.execute(
        select(func.count(FileRecord.id)).where(FileRecord.topic_id == topic_id, FileRecord.deleted_at.is_(None))
    )
    if count_result.scalar_one() >= settings.max_files_per_topic:
        raise HTTPException(status_code=400, detail="File limit reached")

    content = await file.read()
    try:
        validation = validate_file(file.filename, content)
        scan_bytes(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sha256 = sha256_hex(content)
    dup_result = await session.execute(
        select(FileRecord).where(
            FileRecord.topic_id == topic_id,
            FileRecord.sha256 == sha256,
            FileRecord.deleted_at.is_(None),
        )
    )
    if dup_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Duplicate file in topic")

    storage_path, nonce_hex, tag_hex, size_bytes, _ = write_encrypted_file(
        str(topic_id), file.filename, content
    )

    record = FileRecord(
        topic_id=topic_id,
        original_filename=file.filename,
        mime_type=validation.mime_type,
        size_bytes=size_bytes,
        storage_path=storage_path,
        sha256=sha256,
        encryption_nonce=nonce_hex,
        encryption_tag=tag_hex,
        created_at=datetime.utcnow(),
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return FileOut.model_validate(record)


@router.delete("/{file_id}")
async def delete_file_record(
    topic_id: UUID,
    file_id: UUID,
    record: FileRecord = Depends(get_file_for_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    delete_file(record.storage_path)
    record.deleted_at = datetime.utcnow()
    await session.commit()
    return {"status": "deleted"}
