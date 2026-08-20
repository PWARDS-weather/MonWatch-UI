from .common import make_alert_map as _make_alert_map

def make_alert_map(alerts, settings=None, logo_path=None, light=False, filename=None, force_philippines=False):
    return _make_alert_map(alerts, settings=settings, logo_path=logo_path, light=light, filename=filename, force_philippines=force_philippines)
