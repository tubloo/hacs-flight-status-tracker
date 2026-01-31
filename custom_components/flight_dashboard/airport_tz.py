"""Airport timezone helpers.

MVP approach:
- Built-in mapping for common airports
- User overrides via config entry options (airport_timezone_overrides)
- Fallback to Home Assistant local timezone (viewer tz)
"""
from __future__ import annotations

from typing import Any

# Extend this over time. Keep it small + useful.
DEFAULT_AIRPORT_TZ: dict[str, str] = {
    # India
    "DEL": "Asia/Kolkata",
    "BOM": "Asia/Kolkata",
    "BLR": "Asia/Kolkata",
    "MAA": "Asia/Kolkata",
    "HYD": "Asia/Kolkata",
    "CCU": "Asia/Kolkata",
    "AMD": "Asia/Kolkata",
    # Nordics / Europe common
    "CPH": "Europe/Copenhagen",
    "ARN": "Europe/Stockholm",
    "GOT": "Europe/Stockholm",
    "OSL": "Europe/Oslo",
    "HEL": "Europe/Helsinki",
    "FRA": "Europe/Berlin",
    "MUC": "Europe/Berlin",
    "LHR": "Europe/London",
    "LGW": "Europe/London",
    "MAD": "Europe/Madrid",
    "BCN": "Europe/Madrid",
    "CDG": "Europe/Paris",
    "AMS": "Europe/Amsterdam",
    "ZRH": "Europe/Zurich",
    # US
    "LAX": "America/Los_Angeles",
}


def get_airport_tz(iata: str | None, options: dict[str, Any] | None) -> str | None:
    """Return IANA tz string for airport IATA code, if known."""
    if not iata:
        return None
    code = str(iata).strip().upper()
    if not code:
        return None

    opts = options or {}
    overrides = opts.get("airport_timezone_overrides") or {}
    if isinstance(overrides, dict):
        tz = overrides.get(code)
        if isinstance(tz, str) and tz.strip():
            return tz.strip()

    return DEFAULT_AIRPORT_TZ.get(code)
