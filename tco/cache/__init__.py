"""Result Cache: PostgreSQL как источник истины, Redis как быстрый слой."""

from tco.cache.result_cache import CacheHit, ResultCache, get_result_cache

__all__ = ["CacheHit", "ResultCache", "get_result_cache"]
