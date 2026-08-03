"""Result Cache.

Источник истины — таблица в PostgreSQL: in-memory кэш недопустим как
единственное решение при нескольких процессах (SCOPE-R C §9). Redis, если
доступен, работает как быстрый слой перед БД.

Недоступность кэша никогда не является фатальной: при любой ошибке расчет
продолжается без кэша (обязательный failure case).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tco.core.config import Settings, get_settings
from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.db.models.reference import ResultCacheEntry

logger = get_logger(__name__)

REDIS_KEY_PREFIX = "tco:result:"


@dataclass(slots=True)
class CacheHit:
    cache_key: str
    scenario_run_id: str | None
    market_snapshot_id: str | None
    profile_version: str
    expires_at: datetime
    payload: dict[str, Any]
    layer: str

    @property
    def age_seconds(self) -> float:
        return max(0.0, (utcnow() - (self.expires_at - timedelta(seconds=0))).total_seconds())


class ResultCache:
    """Двухслойный кэш результатов расчета."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._redis: Any = None
        self._redis_checked = False

    # ------------------------------------------------------------------ #
    # Redis-слой (опционально)
    # ------------------------------------------------------------------ #

    @property
    def redis(self) -> Any:
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        if not self.settings.redis_url:
            return None
        try:
            import redis as redis_lib

            client = redis_lib.Redis.from_url(
                self.settings.redis_url, socket_timeout=1.0, socket_connect_timeout=1.0
            )
            client.ping()
            self._redis = client
        except Exception as exc:  # noqa: BLE001 — кэш опционален
            logger.info("Redis недоступен, используется только БД-кэш", error=str(exc))
            self._redis = None
        return self._redis

    # ------------------------------------------------------------------ #
    # Чтение
    # ------------------------------------------------------------------ #

    def get(self, session: Session, cache_key: str) -> CacheHit | None:
        if not self.settings.result_cache_enabled:
            return None
        now = utcnow()

        hit = self._get_from_redis(cache_key, now)
        if hit is not None:
            return hit

        try:
            entry = session.scalars(
                select(ResultCacheEntry).where(ResultCacheEntry.cache_key == cache_key)
            ).first()
        except Exception as exc:  # noqa: BLE001 — БД-кэш тоже не фатален
            logger.warning("Не удалось прочитать кэш из БД", error=str(exc))
            return None

        if entry is None:
            return None
        if entry.expires_at <= now:
            return None

        entry.hit_count = int(entry.hit_count or 0) + 1
        return CacheHit(
            cache_key=entry.cache_key,
            scenario_run_id=str(entry.scenario_run_id) if entry.scenario_run_id else None,
            market_snapshot_id=str(entry.market_snapshot_id) if entry.market_snapshot_id else None,
            profile_version=entry.profile_version,
            expires_at=entry.expires_at,
            payload=dict(entry.payload or {}),
            layer="database",
        )

    def _get_from_redis(self, cache_key: str, now: datetime) -> CacheHit | None:
        client = self.redis
        if client is None:
            return None
        try:
            raw = client.get(REDIS_KEY_PREFIX + cache_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ошибка чтения Redis-кэша", error=str(exc))
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            expires_at = datetime.fromisoformat(payload["expires_at"])
        except (ValueError, KeyError, TypeError):
            return None
        if expires_at <= now:
            return None
        return CacheHit(
            cache_key=cache_key,
            scenario_run_id=payload.get("scenario_run_id"),
            market_snapshot_id=payload.get("market_snapshot_id"),
            profile_version=payload.get("profile_version", ""),
            expires_at=expires_at,
            payload=payload.get("payload") or {},
            layer="redis",
        )

    # ------------------------------------------------------------------ #
    # Запись
    # ------------------------------------------------------------------ #

    def put(
        self,
        session: Session,
        *,
        cache_key: str,
        scenario_fingerprint: str,
        profile_version: str,
        scenario_run_id: Any = None,
        market_snapshot_id: Any = None,
        payload: dict[str, Any] | None = None,
        ttl_minutes: int | None = None,
    ) -> datetime | None:
        if not self.settings.result_cache_enabled:
            return None

        ttl = ttl_minutes or self.settings.result_cache_ttl_minutes
        expires_at = utcnow() + timedelta(minutes=ttl)
        body = payload or {}

        try:
            entry = session.scalars(
                select(ResultCacheEntry).where(ResultCacheEntry.cache_key == cache_key)
            ).first()
            if entry is None:
                entry = ResultCacheEntry(cache_key=cache_key, hit_count=0)
                session.add(entry)
            entry.scenario_fingerprint = scenario_fingerprint
            entry.profile_version = profile_version
            entry.scenario_run_id = scenario_run_id
            entry.market_snapshot_id = market_snapshot_id
            entry.payload = body
            entry.expires_at = expires_at
            session.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось записать кэш в БД", error=str(exc))

        client = self.redis
        if client is not None:
            try:
                client.setex(
                    REDIS_KEY_PREFIX + cache_key,
                    ttl * 60,
                    json.dumps(
                        {
                            "scenario_run_id": str(scenario_run_id) if scenario_run_id else None,
                            "market_snapshot_id": str(market_snapshot_id)
                            if market_snapshot_id
                            else None,
                            "profile_version": profile_version,
                            "expires_at": expires_at.isoformat(),
                            "payload": body,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Не удалось записать Redis-кэш", error=str(exc))

        return expires_at

    # ------------------------------------------------------------------ #
    # Инвалидация
    # ------------------------------------------------------------------ #

    def invalidate(self, session: Session, cache_key: str) -> bool:
        client = self.redis
        if client is not None:
            try:
                client.delete(REDIS_KEY_PREFIX + cache_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Не удалось удалить Redis-ключ", error=str(exc))
        result = session.execute(
            delete(ResultCacheEntry).where(ResultCacheEntry.cache_key == cache_key)
        )
        return bool(result.rowcount)

    def purge_all(self, session: Session) -> int:
        client = self.redis
        if client is not None:
            try:
                for key in client.scan_iter(match=REDIS_KEY_PREFIX + "*", count=500):
                    client.delete(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Не удалось очистить Redis-кэш", error=str(exc))
        result = session.execute(delete(ResultCacheEntry))
        return int(result.rowcount or 0)

    def stats(self, session: Session) -> dict[str, Any]:
        entries = session.scalars(select(ResultCacheEntry)).all()
        now = utcnow()
        active = [entry for entry in entries if entry.expires_at > now]
        total_hits = sum(int(entry.hit_count or 0) for entry in entries)
        return {
            "entries": len(entries),
            "active_entries": len(active),
            "total_hits": total_hits,
            "redis_available": self.redis is not None,
            "ttl_minutes": self.settings.result_cache_ttl_minutes,
            "enabled": self.settings.result_cache_enabled,
        }


_cache: ResultCache | None = None


def get_result_cache() -> ResultCache:
    global _cache
    if _cache is None:
        _cache = ResultCache()
    return _cache


def reset_result_cache() -> None:
    global _cache
    _cache = None
