"""MonWatch-UI module entry point.

Allows launching via ``python -m monwatch``. The real implementation lives in
the ``src`` package (kept for backwards compatibility with ``python -m src``).
"""

from src.UI import main

__all__ = ["main"]
