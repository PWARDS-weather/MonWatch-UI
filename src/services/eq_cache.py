from src.services._cache_base import ProjectionCache

__all__ = ["eq_cache"]

eq_cache = ProjectionCache(name="eq", max_mb=512, log_func=None)
