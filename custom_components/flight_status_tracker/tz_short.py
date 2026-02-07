"""Server-side timezone abbreviation helpers.

We store tz (IANA long) and tz_short (abbrev/offset) per airport in sensor attrs.
Viewer TZ must NOT be stored server-side (can differ per user).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util


def _to_dt(val) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        dt = dt_util.parse_datetime(val)
        if dt is None:
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                return None
        return dt
    return None


def tz_short_name(tz_name: str | None, when) -> str | None:
    """Return a short TZ label like 'CET', 'IST', '-03', '+05:30'.

    Uses 'when' so DST is correct. Returns None if unknown.
    """
    if not tz_name:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return None

    dt = _to_dt(when) or dt_util.utcnow()
    try:
        dt_utc = dt_util.as_utc(dt) if dt.tzinfo else dt_util.as_utc(dt_util.as_local(dt))
        local = dt_utc.astimezone(tz)
    except Exception:
        return None

    abbr = local.tzname()
    if abbr:
        return abbr

    off = local.utcoffset()
    if off is None:
        return None
    total_min = int(off.total_seconds() // 60)
    sign = "+" if total_min >= 0 else "-"
    total_min = abs(total_min)
    hh = total_min // 60
    mm = total_min % 60
    return f"{sign}{hh:02d}:{mm:02d}"
