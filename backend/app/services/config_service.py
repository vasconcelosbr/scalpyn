import json
import logging
from uuid import UUID
from typing import Dict, Any, Optional
from sqlalchemy import select, update, insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.config_profile import ConfigProfile, ConfigAuditLog
from ..config import settings

logger = logging.getLogger(__name__)

_VALID_SCHEMES = ("redis://", "rediss://", "unix://")


def _make_redis_client():
    """Build async Redis client — returns None if URL is missing or invalid."""
    import redis.asyncio as redis_lib
    url = (settings.REDIS_URL or "").strip()
    if not url:
        logger.warning("REDIS_URL is not set — running config service without cache.")
        return None
    if not any(url.startswith(s) for s in _VALID_SCHEMES):
        logger.warning(
            "REDIS_URL has no recognised scheme (redis://, rediss://, unix://) — "
            "got %r — running without cache.",
            url[:40],
        )
        return None
    try:
        client = redis_lib.from_url(url, decode_responses=True)
        return client
    except Exception as exc:
        logger.warning("Failed to create Redis client (%s) — running without cache.", exc)
        return None


class ConfigService:
    def __init__(self):
        self.redis = _make_redis_client()

    def _get_cache_key(self, config_type: str, user_id: UUID, pool_id: Optional[UUID] = None) -> str:
        if pool_id:
            return f"config:{user_id}:{pool_id}:{config_type}"
        return f"config:{user_id}:global:{config_type}"

    async def get_config(self, db: AsyncSession, config_type: str, user_id: UUID, pool_id: Optional[UUID] = None) -> Dict[str, Any]:
        cache_key = self._get_cache_key(config_type, user_id, pool_id)

        if self.redis:
            try:
                cached_config = await self.redis.get(cache_key)
                if cached_config:
                    return json.loads(cached_config)
            except Exception as e:
                logger.warning("Redis cache read failed (skipping cache): %s", e)

        query = select(ConfigProfile).where(
            ConfigProfile.user_id == user_id,
            ConfigProfile.pool_id == pool_id,
            ConfigProfile.config_type == config_type
        )
        result = await db.execute(query)
        profile = result.scalars().first()

        if profile:
            if self.redis:
                try:
                    await self.redis.set(cache_key, json.dumps(profile.config_json), ex=3600)
                except Exception as e:
                    logger.warning("Redis cache write failed (skipping cache): %s", e)
            return profile.config_json

        return {}

    async def update_config(self, db: AsyncSession, config_type: str, user_id: UUID, new_json: Dict[str, Any], changed_by: UUID, pool_id: Optional[UUID] = None, change_description: str = "") -> Dict[str, Any]:
        query = select(ConfigProfile).where(
            ConfigProfile.user_id == user_id,
            ConfigProfile.pool_id == pool_id,
            ConfigProfile.config_type == config_type
        )
        result = await db.execute(query)
        profile = result.scalars().first()

        previous_json = None

        if profile:
            previous_json = profile.config_json
            profile.config_json = new_json
        else:
            profile = ConfigProfile(
                user_id=user_id,
                pool_id=pool_id,
                config_type=config_type,
                config_json=new_json
            )
            db.add(profile)
        await db.flush()

        audit_log = ConfigAuditLog(
            config_id=profile.id,
            changed_by=changed_by,
            previous_json=previous_json,
            new_json=new_json,
            change_description=change_description
        )
        db.add(audit_log)

        await db.commit()

        cache_key = self._get_cache_key(config_type, user_id, pool_id)
        if self.redis:
            try:
                await self.redis.delete(cache_key)
            except Exception as e:
                logger.warning("Redis cache invalidation failed (skipping): %s", e)

        return new_json


config_service = ConfigService()
