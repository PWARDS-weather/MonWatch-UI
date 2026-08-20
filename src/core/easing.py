import math
from typing import Callable


class Easing:
    _registry: dict[str, Callable[[float], float]] = {}

    @classmethod
    def register(cls, name: str, func: Callable[[float], float]):
        cls._registry[name] = func

    @classmethod
    def apply(cls, name: str, t: float) -> float:
        t = max(0.0, min(1.0, t))
        func = cls._registry.get(name)
        if func is None:
            return t
        return func(t)

    @classmethod
    def list_names(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def register_cubic_bezier(cls, name: str, x1: float, y1: float, x2: float, y2: float):
        def _cubic_bezier(t: float) -> float:
            t = max(0.0, min(1.0, t))
            return _cubic_bezier_sample(x1, y1, x2, y2, t)
        cls._registry[name] = _cubic_bezier

    @classmethod
    def parse_and_register(cls, name: str, easing_str: str) -> bool:
        if easing_str.startswith("cubic_bezier(") and easing_str.endswith(")"):
            try:
                args = easing_str[len("cubic_bezier("):-1]
                parts = [float(p.strip()) for p in args.split(",")]
                if len(parts) == 4:
                    cls.register_cubic_bezier(name, *parts)
                    return True
            except (ValueError, IndexError):
                pass
        return False


def _cubic_bezier_sample(x1, y1, x2, y2, t):
    def _sample_t(t):
        return 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t ** 2 * x2 + t ** 3

    t0, t1 = 0.0, 1.0
    for _ in range(20):
        mid = (t0 + t1) / 2
        x_mid = _sample_t(mid)
        if abs(x_mid - t) < 1e-6:
            break
        if x_mid < t:
            t0 = mid
        else:
            t1 = mid
    t_est = (t0 + t1) / 2
    return 3 * (1 - t_est) ** 2 * t_est * y1 + 3 * (1 - t_est) * t_est ** 2 * y2 + t_est ** 3


Easing.register("linear", lambda t: t)
Easing.register("ease_in_quad", lambda t: t * t)
Easing.register("ease_out_quad", lambda t: t * (2 - t))
Easing.register("ease_in_out_quad", lambda t: 2 * t * t if t < 0.5 else -1 + (4 - 2 * t) * t)
Easing.register("ease_in_cubic", lambda t: t * t * t)
Easing.register("ease_out_cubic", lambda t: (t - 1) ** 3 + 1)
Easing.register("ease_in_out_cubic", lambda t: 4 * t * t * t if t < 0.5 else (t - 1) * (2 * t - 2) * (2 * t - 2) + 1)
Easing.register("ease_in_sine", lambda t: 1 - math.cos(t * math.pi / 2))
Easing.register("ease_out_sine", lambda t: math.sin(t * math.pi / 2))
Easing.register("ease_in_out_sine", lambda t: -(math.cos(math.pi * t) - 1) / 2)
Easing.register("ease_in_out_elastic", lambda t: _elastic(t))
Easing.register("ease_in_out_bounce", lambda t: _bounce(t))

def _elastic(t):
    if t == 0 or t == 1:
        return t
    return -2 ** (10 * t - 10) * math.sin((t * 10 - 10.75) * (2 * math.pi / 3)) + 1 if t > 0.5 else 2 ** (10 * t - 10) * math.sin((t * 10 - 10.75) * (2 * math.pi / 3))

def _bounce(t):
    if t < 0.3636: return 7.5625 * t * t
    if t < 0.7273: return 7.5625 * (t - 0.5455) ** 2 + 0.75
    if t < 0.9091: return 7.5625 * (t - 0.8182) ** 2 + 0.9375
    return 7.5625 * (t - 0.9545) ** 2 + 0.984375
