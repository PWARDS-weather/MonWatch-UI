"""
MonWatch-UI Cyclone Forecast Package.

Forecast generation modules:
  common.py            — Main CLI controller (dispatches to layout handlers)
  utils.py             — Shared helper functions and constants
  shared_flow.py       — Common drawing infrastructure used by all handlers
  default_handler.py   — Default/fallback forecast layout
  pagasa_handler.py    — PAGASA-style forecast layout
  nhc_handler.py       — NHC-style forecast layout (uses tropycal)
  jma_handler.py       — JMA-style forecast layout
  jtwc_handler.py      — JTWC-style forecast layout
"""

# Backward-compatible imports: allow `from Process.forecast.pagasa import make_forecast`
from .pagasa_handler import make_forecast as _pag_forecast, make_combined_forecast as _pag_combined
from .nhc_handler import make_forecast as _nhc_forecast, make_combined_forecast as _nhc_combined
from .jma_handler import make_forecast as _jma_forecast, make_combined_forecast as _jma_combined
from .jtwc_handler import make_forecast as _jtwc_forecast, make_combined_forecast as _jtwc_combined
from .default_handler import make_forecast as _default_forecast, make_combined_forecast as _default_combined
