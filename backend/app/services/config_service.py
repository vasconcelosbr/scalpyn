import json
import logging
from uuid import UUID
from typing import Dict, Any, Optional
from sqlalchemy import select, update, insert
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis

from ..models.config_profile import ConfigProfile, ConfigAuditLog
from ..config import settings

logger = logging.getLogger(__name__)

class ConfigService:
    def __init__(self):
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)

    def _get_cache_key(self, config_type: str, user_id: UUID, pool_id: Optional[UUID] = None) -> str:
        if pool_id:
            return f"config:{user_id}:{pool_id}:{config_type}"
        return f"config:{user_id}:global:{config_type}"

    async def get_config(self, db: AsyncSession, config_type: str, user_id: UUID, pool_id: Optional[UUID] = None) -> Dict[str, Any]:
        cache_key = self._get_cache_key(config_type, user_id, pool_id)
        try:
            cached_config = await self.redis.get(cache_key)
            if cached_config:
                return json.loads(cached_config)
        except Exception as e:
            logger.warning(f"Redis cache read failed (skipping cache): {e}")

        query = select(ConfigProfile).where(
            ConfigProfile.user_id == user_id,
            ConfigProfile.pool_id == pool_id,
            ConfigProfile.config_type == config_type
        )
        result = await db.execute(query)
        profile = result.scalars().first()

        if profile:
            try:
                await self.redis.set(cache_key, json.dumps(profile.config_json), ex=3600)
            except Exception as e:
                logger.warning(f"Redis cache write failed (skipping cache): {e}")
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

        # Create Audit Log
        audit_log = ConfigAuditLog(
            config_id=profile.id,
            changed_by=changed_by,
            previous_json=previous_json,
            new_json=new_json,
            change_description=change_description
        )
        db.add(audit_log)

        await db.commit()

        # Invalidate Cache
        cache_key = self._get_cache_key(config_type, user_id, pool_id)
        try:
            await self.redis.delete(cache_key)
        except Exception as e:
            logger.warning(f"Redis cache invalidation failed (skipping): {e}")

        return new_json

config_service = ConfigService()
