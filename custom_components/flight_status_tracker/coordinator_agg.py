"""Aggregation/merge helpers.

All segments are plain dicts (no dataclasses). This avoids earlier
AttributeError issues when providers return dicts.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _iso(val: Any) -> str | None:
    """Return ISO string for datetime/iso input."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, str):
        return val
    return None


def flight_key(seg: dict[str, Any]) -> str:
    """Stable key for merging duplicates across providers."""
    return (
        seg.get("flight_key")
        or f"{seg.get('airline_code','')}-{seg.get('flight_number','')}-{seg.get('dep',{}).get('airport',{}).get('iata','')}-{seg.get('dep',{}).get('scheduled','')}"
    )


def merge_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge duplicate flights by flight_key.

    Rules:
    - travellers union (keep order, unique)
    - pick non-null values, prefer ones that look more complete
    - status timestamps: choose actual > estimated > scheduled if present
    """
    merged: dict[str, dict[str, Any]] = {}

    for seg in segments:
        key = flight_key(seg)
        cur = merged.get(key)
        if not cur:
            merged[key] = seg
            continue

        # travellers union
        a = cur.get("travellers") or []
        b = seg.get("travellers") or []
        seen = set()
        out = []
        for t in a + b:
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        cur["travellers"] = out

        # shallow merge: fill missing top-level fields
        for k, v in seg.items():
            if cur.get(k) in (None, "", [], {}):
                cur[k] = v

        # merge dep/arr blocks
        for side in ("dep", "arr"):
            cur.setdefault(side, {})
            seg_side = seg.get(side) or {}
            cur_side = cur.get(side) or {}

            # airport identity
            cur_side.setdefault("airport", {})
            seg_air = seg_side.get("airport") or {}
            cur_air = cur_side.get("airport") or {}

            for k, v in seg_air.items():
                if cur_air.get(k) in (None, "", [], {}):
                    cur_air[k] = v
            cur_side["airport"] = cur_air

            # timestamps: scheduled/estimated/actual
            for tkey in ("scheduled", "estimated", "actual"):
                if cur_side.get(tkey) in (None, "") and seg_side.get(tkey):
                    cur_side[tkey] = seg_side.get(tkey)

            # terminal/gate
            for k in ("terminal", "gate"):
                if cur_side.get(k) in (None, "") and seg_side.get(k):
                    cur_side[k] = seg_side.get(k)

            cur[side] = cur_side

        # status_state: prefer non-unknown
        cur_state = (cur.get("status_state") or "").strip().lower()
        if (cur_state in ("", "unknown")) and seg.get("status_state"):
            cur["status_state"] = seg.get("status_state")

    # sort by departure scheduled time (string ISO sorts ok if all have offsets; safer parse elsewhere)
    out = list(merged.values())
    out.sort(key=lambda x: (x.get("dep", {}).get("scheduled") or ""))
    return out
