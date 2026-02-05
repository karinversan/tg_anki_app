from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, verify_telegram_init_data
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])


class TelegramAuthRequest(BaseModel):
    init_data: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

@router.post("/telegram", response_model=TokenResponse)
async def auth_telegram(payload: TelegramAuthRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    try:
        parsed = verify_telegram_init_data(payload.init_data, settings.bot_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user_data = parsed.get("user")
    if not user_data:
        raise HTTPException(status_code=400, detail="Missing user data")

    telegram_id = int(user_data.get("id"))
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(telegram_id=telegram_id)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token)
