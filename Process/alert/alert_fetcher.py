"""
Standalone entry point for fetching and parsing PAGASA/NWS weather alerts
in an external OS process. Outputs JSON array of parsed alerts to stdout.

Usage:
    python Process/alert/alert_fetcher.py --sources NWS,PAGASA
    python Process/alert/alert_fetcher.py --sources PAGASA --output-file alerts.json
    python Process/alert/alert_fetcher.py --sources NWS --lat 14.5 --lon 121.0 --zone PHZ001
"""

import sys
import json
import os
import argparse
from pathlib import Path

# Ensure top-level project dir is on sys.path so `import src.xxx` works
_script_dir = Path(__file__).resolve().parent
_project_dir = str(_script_dir.parent.parent)
if _project_dir not in sys.path:
    sys.path.insert(0, _project_dir)


def fetch_nws_alerts(lat=None, lon=None, zone=None, marine=False):
    """Fetch and parse NWS weather.gov alerts."""
    from src.clients.weathergov import (
        fetch_alerts,
        fetch_alerts_for_point,
        fetch_alerts_for_zone,
        fetch_active_alerts_for_marine,
        parse_alert,
    )

    try:
        raw = []
        if marine:
            raw = fetch_active_alerts_for_marine()
        elif zone:
            raw = fetch_alerts_for_zone(zone)
        elif lat is not None and lon is not None:
            raw = fetch_alerts_for_point(lat, lon)
        else:
            raw = fetch_alerts(severity="Severe")

        parsed = [parse_alert(f) for f in raw]
        return parsed
    except Exception as e:
        print(f"NWS fetch error: {e}", file=sys.stderr)
        return []


def fetch_pagasa_alerts():
    """Fetch and parse PAGASA CAP alerts."""
    from src.clients.pagasa_alerts import fetch_pagasa_alerts, parse_pagasa_alert

    try:
        raw = fetch_pagasa_alerts()
        parsed = [parse_pagasa_alert(a) for a in raw]
        return [a for a in parsed if a is not None]
    except Exception as e:
        print(f"PAGASA fetch error: {e}", file=sys.stderr)
        return []


def severity_score(alert):
    sev = {"Extreme": 5, "Severe": 4, "Moderate": 3, "Minor": 2, "Unknown": 1}.get(
        alert.get("severity", ""), 0
    )
    urg = {"Immediate": 5, "Expected": 4, "Future": 3, "Past": 2, "Unknown": 1}.get(
        alert.get("urgency", ""), 0
    )
    return sev + urg


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and parse PAGASA/NWS weather alerts in a subprocess."
    )
    parser.add_argument(
        "--sources",
        default="NWS,PAGASA",
        help="Comma-separated alert sources (NWS, PAGASA). Default: NWS,PAGASA",
    )
    parser.add_argument("--lat", type=float, help="Latitude for point-based NWS alerts")
    parser.add_argument("--lon", type=float, help="Longitude for point-based NWS alerts")
    parser.add_argument("--zone", help="NWS zone ID (e.g. PHZ001)")
    parser.add_argument(
        "--marine", action="store_true", help="Fetch marine alerts only"
    )
    parser.add_argument(
        "--output-file",
        help="Write JSON to file instead of stdout (for file-based IPC)",
    )
    args = parser.parse_args()

    sources = [s.strip().upper() for s in args.sources.split(",")]

    all_alerts = []

    if "NWS" in sources:
        nws = fetch_nws_alerts(
            lat=args.lat, lon=args.lon, zone=args.zone, marine=args.marine
        )
        for a in nws:
            a["source"] = "NWS"
        all_alerts.extend(nws)

    if "PAGASA" in sources:
        pagasa = fetch_pagasa_alerts()
        for a in pagasa:
            a["source"] = "PAGASA"
        all_alerts.extend(pagasa)

    # Sort by severity descending
    all_alerts.sort(key=severity_score, reverse=True)

    output = json.dumps(all_alerts, indent=2, default=str)

    if args.output_file:
        out_path = Path(args.output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"Wrote {len(all_alerts)} alerts to {args.output_file}", file=sys.stderr)
    else:
        sys.stdout.write(output)

    # Exit with status
    if not all_alerts:
        sys.exit(0)


if __name__ == "__main__":
    main()
