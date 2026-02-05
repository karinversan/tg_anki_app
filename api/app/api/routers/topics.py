from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_topic_for_user
from app.db.models import FileRecord, Topic, User
from app.db.session import get_session
from app.schemas.topic import TopicCreate, TopicOut, TopicUpdate
from app.services.cache import get_redis
from app.services.topics import remove_topic_assets

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/", response_model=list[TopicOut])
async def list_topics(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TopicOut]:
    redis = get_redis()
    cache_key = f"topics:{user.id}"
    try:
        cached = await redis.get(cache_key)
    except Exception:
        cached = None
    if cached:
        return [TopicOut.model_validate_json(item) for item in cached.split("\n") if item]

    stmt = (
        select(Topic, func.count(FileRecord.id))
        .outerjoin(FileRecord, FileRecord.topic_id == Topic.id)
        .where(Topic.user_id == user.id)
        .group_by(Topic.id)
        .order_by(Topic.created_at.desc())
    )
    result = await session.execute(stmt)
    topics = []
    for topic, file_count in result.all():
        data = TopicOut(
            id=topic.id,
            user_id=topic.user_id,
            title=topic.title,
            created_at=topic.created_at,
            updated_at=topic.updated_at,
            file_count=file_count,
        )
        topics.append(data)

    try:
        await redis.set(cache_key, "\n".join(t.model_dump_json() for t in topics), ex=10)
    except Exception:
        pass
    return topics


@router.post("/", response_model=TopicOut)
async def create_topic(
    payload: TopicCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TopicOut:
    topic = Topic(user_id=user.id, title=payload.title, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    session.add(topic)
    await session.commit()
    await session.refresh(topic)
    try:
        await get_redis().delete(f"topics:{user.id}")
    except Exception:
        pass
    return TopicOut(
        id=topic.id,
        user_id=topic.user_id,
        title=topic.title,
        created_at=topic.created_at,
        updated_at=topic.updated_at,
        file_count=0,
    )


@router.delete("/{topic_id}")
async def delete_topic(
    topic: Topic = Depends(get_topic_for_user),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await session.refresh(topic)
    await remove_topic_assets(session, topic)
    await session.delete(topic)
    await session.commit()
    try:
        await get_redis().delete(f"topics:{user.id}")
    except Exception:
        pass
    return {"status": "deleted"}


@router.patch("/{topic_id}", response_model=TopicOut)
async def update_topic(
    payload: TopicUpdate,
    topic: Topic = Depends(get_topic_for_user),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TopicOut:
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")
    topic.title = payload.title
    topic.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(topic)
    try:
        await get_redis().delete(f"topics:{user.id}")
    except Exception:
        pass
    return TopicOut(
        id=topic.id,
        user_id=topic.user_id,
        title=topic.title,
        created_at=topic.created_at,
        updated_at=topic.updated_at,
        file_count=await session.scalar(select(func.count(FileRecord.id)).where(FileRecord.topic_id == topic.id)) or 0,
    )
