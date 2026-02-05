from __future__ import annotations

from redis.asyncio import Redis
from redis.exceptions import RedisError

async def check_rate_limit(redis: Redis, key: str, limit: int, window_seconds: int) -> None:
    try:
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, window_seconds)
        if current > limit:
            raise ValueError("Rate limit exceeded")
    except RedisError:
        return
