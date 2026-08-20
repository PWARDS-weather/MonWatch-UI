from src.services._cache_base import ProjectionCache

__all__ = ["fd_cache"]

fd_cache = ProjectionCache(name="fd", max_mb=1024, log_func=None)
