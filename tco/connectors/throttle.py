"""Ограничение темпа обращений к источнику.

Зачем это нужно. Плановый сбор устроен пачками: раз в час в очередь
уходит по два вызова на каждый активный сценарий. При 110 сценариях это
свыше 250 обращений, которые воркеры разбирают параллельно. Публичные
источники (Туту, РЖД) не публикуют лимитов, поэтому ограничивать темп
обязаны мы сами — иначе всплеск нагрузки рискует получить 429 или молчаливую
блокировку по IP.

Реализация — token bucket:

* при доступном Redis ведро общее для всех воркеров и процессов;
* при недоступном — локальное для процесса, с явной записью в лог.

Локальное ведро слабее, но лучше отсутствия ограничения: даже один воркер
без него способен выдать пачку в несколько десятков запросов в секунду.

Ограничитель никогда не отменяет запрос: он только задерживает его. Если
ожидание превышает бюджет вызова, управление возвращается вызывающему коду,
который решает, что делать (обычно — считать источник недоступным).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from tco.core.config import Settings, get_settings
from tco.core.logging import get_logger

logger = get_logger(__name__)

REDIS_KEY_PREFIX = "tco:throttle:"

#: Скрипт token bucket. Выполняется атомарно на стороне Redis, поэтому
#: гонок между воркерами не возникает. Возвращает время ожидания в
#: миллисекундах: 0 — можно выполнять запрос немедленно.
_LUA_TOKEN_BUCKET = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts = tonumber(bucket[2])

if tokens == nil then
  tokens = capacity
  ts = now
end

local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * rate)

local wait = 0
if tokens >= 1 then
  tokens = tokens - 1
else
  wait = math.ceil(((1 - tokens) / rate) * 1000)
end

redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, ttl)
return wait
"""


@dataclass(slots=True)
class _LocalBucket:
    """Ведро в пределах процесса — запасной вариант без Redis."""

    rate_per_second: float
    capacity: float
    tokens: float = field(default=0.0)
    updated_at: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def acquire_wait(self) -> float:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(
                self.capacity, self.tokens + (now - self.updated_at) * self.rate_per_second
            )
            self.updated_at = now
            if self.tokens >= 1:
                self.tokens -= 1
                return 0.0
            return (1 - self.tokens) / self.rate_per_second


class SourceThrottle:
    """Ограничитель темпа для одного источника."""

    def __init__(
        self,
        source_code: str,
        *,
        rate_per_minute: int | None,
        burst: int | None = None,
        settings: Settings | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.source_code = source_code
        self.settings = settings or get_settings()
        self.rate_per_minute = rate_per_minute
        # Всплеск по умолчанию — четверть минутного лимита, но не меньше 1:
        # это позволяет начать сбор без задержки, не выдавая залп целиком.
        self.burst = burst or max(1, int((rate_per_minute or 0) / 4))
        self._sleep = sleep
        self._local: _LocalBucket | None = None
        self._redis = None
        self._redis_checked = False
        self._script = None

    @property
    def enabled(self) -> bool:
        return bool(self.rate_per_minute and self.rate_per_minute > 0)

    # ------------------------------------------------------------------ #
    # Redis
    # ------------------------------------------------------------------ #

    def _get_redis(self):  # noqa: ANN202
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
            self._script = client.register_script(_LUA_TOKEN_BUCKET)
            self._redis = client
        except Exception as exc:  # noqa: BLE001 — ограничитель не должен падать
            logger.warning(
                "Redis недоступен: ограничение темпа источника работает только в пределах процесса",
                source=self.source_code,
                error=str(exc),
            )
            self._redis = None
        return self._redis

    def _wait_seconds(self) -> float:
        """Сколько нужно подождать перед следующим запросом."""
        rate_per_second = (self.rate_per_minute or 0) / 60.0
        if rate_per_second <= 0:
            return 0.0

        client = self._get_redis()
        if client is not None and self._script is not None:
            try:
                wait_ms = self._script(
                    keys=[REDIS_KEY_PREFIX + self.source_code],
                    args=[rate_per_second, self.burst, time.time(), 3600],
                )
                return max(0.0, float(wait_ms) / 1000.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Ошибка распределенного ограничителя, переход на локальный",
                    source=self.source_code,
                    error=str(exc),
                )
                self._redis = None

        if self._local is None:
            self._local = _LocalBucket(
                rate_per_second=rate_per_second, capacity=float(self.burst)
            )
        return self._local.acquire_wait()

    # ------------------------------------------------------------------ #
    # Публичный интерфейс
    # ------------------------------------------------------------------ #

    def acquire(self, *, budget_seconds: float | None = None) -> float:
        """Дожидается права на запрос. Возвращает фактическую задержку.

        ``budget_seconds`` ограничивает ожидание: если разрешения пришлось бы
        ждать дольше, метод возвращает управление немедленно, а вызывающий код
        решает, продолжать ли. Так ограничитель не съедает бюджет вызова.
        """
        if not self.enabled:
            return 0.0

        wait = self._wait_seconds()
        if wait <= 0:
            return 0.0
        if budget_seconds is not None and wait > budget_seconds:
            logger.info(
                "Ожидание ограничителя превышает бюджет вызова",
                source=self.source_code,
                wait_seconds=round(wait, 2),
                budget_seconds=round(budget_seconds, 2),
            )
            return wait

        logger.debug(
            "Задержка по ограничителю темпа",
            source=self.source_code,
            wait_seconds=round(wait, 2),
        )
        self._sleep(wait)
        return wait


#: Ограничители переиспользуются в пределах процесса: локальное ведро иначе
#: создавалось бы заново на каждый вызов и не ограничивало бы ничего.
_throttles: dict[str, SourceThrottle] = {}
_throttles_lock = threading.Lock()


def get_throttle(
    source_code: str, rate_per_minute: int | None, settings: Settings | None = None
) -> SourceThrottle:
    """Возвращает ограничитель источника, создавая его при первом обращении."""
    with _throttles_lock:
        existing = _throttles.get(source_code)
        if existing is not None and existing.rate_per_minute == rate_per_minute:
            return existing
        throttle = SourceThrottle(
            source_code, rate_per_minute=rate_per_minute, settings=settings
        )
        _throttles[source_code] = throttle
        return throttle


def reset_throttles() -> None:
    """Сбрасывает кэш ограничителей (используется в тестах)."""
    with _throttles_lock:
        _throttles.clear()
