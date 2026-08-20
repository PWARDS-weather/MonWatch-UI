from src.services._cache_base import ProjectionCache

__all__ = ["fc_cache"]

fc_cache = ProjectionCache(name="fc", max_mb=256, log_func=None)
