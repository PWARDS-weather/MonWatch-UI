import logging

_log = logging.getLogger(__name__)

_ENGINE_CACHE = {}
_PRODUCTS_CACHE = {}

def _resolve_target(sat_name: str) -> str:
    s = sat_name.lower() if sat_name else ""
    if "goes" in s:
        return "goes"
    elif "gk2a" in s or "gk-2a" in s:
        return "gk2a"
    elif "meteosat" in s or "msg" in s:
        return "meteosat"
    else:
        return "himawari"

_MODULE_MAP = {
    "goes":     (".GOES_Engine",     ".GOES_Products"),
    "gk2a":     (".GK2A_Engine",     ".GK2A_Products"),
    "meteosat": (".EU_Engine",       ".EU_Products"),
    "himawari": (".HIM_Engine",      ".HIM_Products"),
}

def get_engine(sat_name: str):
    """Return the EmbeddedRGBEngine class for the given satellite name."""
    target = _resolve_target(sat_name)
    if target not in _ENGINE_CACHE:
        import importlib
        eng_mod_name, _ = _MODULE_MAP[target]
        mod = importlib.import_module(eng_mod_name, package="src.core")
        _ENGINE_CACHE[target] = mod.EmbeddedRGBEngine
        n_products = len(get_products(sat_name))
        _log.info("Engine dispatch [%s] -> %s.EmbeddedRGBEngine (%d products)",
                  sat_name, mod.__name__, n_products)
    return _ENGINE_CACHE[target]

def get_products(sat_name: str):
    """Return the RGB_PRODUCTS dict for the given satellite name."""
    target = _resolve_target(sat_name)
    if target not in _PRODUCTS_CACHE:
        import importlib
        _, prod_mod_name = _MODULE_MAP[target]
        mod = importlib.import_module(prod_mod_name, package="src.core")
        _PRODUCTS_CACHE[target] = mod.RGB_PRODUCTS
    return _PRODUCTS_CACHE[target]

def get_tag_colors():
    from .HIM_Products import _TAG_COLORS
    return _TAG_COLORS
