"""
Single source of truth for category bucketing.

Previously the project had TWO copies of `broad_category` — one in app.py
(strict, English-keyword only) and one in zone_aggregator._broad
(permissive, handles LG ERP short codes + Korean + Home Entertainment
case quirks). The two functions classified the same Division-name string
differently, which is exactly what surfaced the "Other × 16" leak the
CEO reported on 2026-05-18.

This module is the merged matcher. app.py and zone_aggregator both
import from here. ``user_overrides`` reads any
``data/category_overrides.json`` so dispatchers can self-map division
strings without a code release (Agent 3 audit recommendation).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict


_OVERRIDES: Dict[str, str] | None = None


def _load_overrides() -> Dict[str, str]:
    """Read ``data/category_overrides.json`` once per process.

    Schema::

        { "COOKING_RANGE": "oven",
          "RAS-SPLIT":     "ac",
          "Home Entertainment": "tv" }

    Keys are matched case-insensitively after the same normalisation
    `broad_category` applies (lower + strip).
    """
    global _OVERRIDES
    if _OVERRIDES is not None:
        return _OVERRIDES
    candidate = Path(__file__).resolve().parent.parent / "data" / "category_overrides.json"
    out: Dict[str, str] = {}
    if candidate.exists():
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                out = {str(k).strip().lower(): str(v).strip().lower() for k, v in raw.items()}
        except Exception:
            out = {}
    _OVERRIDES = out
    return _OVERRIDES


def reload_overrides() -> None:
    """Force the next ``broad_category`` call to re-read the overrides file.

    The dispatcher can edit the JSON during a session and click "Reload" in
    the diagnostic panel to apply without restarting the .exe.
    """
    global _OVERRIDES
    _OVERRIDES = None


def broad_category(cat: str) -> str:
    """Map a raw Division-name string → broad bucket key.

    Returns one of: refrigerator, washer, dryer, dishwasher, microwave,
    oven, tv, monitor, av, ac, other.

    Recognises:
      - User overrides in ``data/category_overrides.json``
      - Korean: 냉장고/세탁기/건조기/식기/전자레인지/오븐/쿡탑/모니터/티비/공조 …
      - English substrings (longest-first to avoid "washer" matching "dishwasher")
      - LG ERP short codes (H/A, HE, AV, RAC, REF, RFG, WM, DR, DW, MW, OV, WO, TV, MN)
      - First-token short codes (e.g. "REF-CD-001" → refrigerator)
    """
    raw = cat or ""
    c = raw.lower().strip()
    if not c:
        return "other"

    overrides = _load_overrides()
    if c in overrides:
        return overrides[c]

    # ── Korean keywords (full) ──
    if "냉장고" in raw: return "refrigerator"
    if "세탁기" in raw: return "washer"
    if "건조기" in raw: return "dryer"
    if "식기" in raw:   return "dishwasher"
    if "전자레인지" in raw or "전자렌지" in raw or "마이크로웨이브" in raw: return "microwave"
    if "오븐" in raw or "월오븐" in raw: return "oven"
    if "쿡탑" in raw or "렌지" in raw or "레인지" in raw or "스토브" in raw or "조리" in raw or "쿠킹" in raw: return "oven"
    if "모니터" in raw: return "monitor"
    if "텔레비전" in raw or "티비" in raw: return "tv"
    if "에어컨" in raw or "공조" in raw: return "ac"
    if "오디오" in raw or "스피커" in raw or "사운드바" in raw: return "av"

    # ── English substring matching ──
    # Order matters: more-specific keys BEFORE less-specific
    # ("dishwasher" before "washer", "monitor" before "tv").
    refrig_terms = ("refrigerator", "refrigeration", "fridge", "rfg", "ref-",
                    "ref_", "rf-", "rf_")
    dish_terms   = ("dishwash", "dw-", "dw_")
    washer_terms = ("washer", "washing", "laundry-w", "laundry_w",
                    "wash machine", "front load", "top load", "wash_")
    dryer_terms  = ("dryer", "laundry-d", "laundry_d", "drying")
    micro_terms  = ("microwave", "mwo", "otr",
                    "countertop microwave", "mw_", "mw-")
    monitor_terms= ("monitor", "gaming display", "ips display", "mon-", "mon_")
    tv_terms     = ("television", " tv", "oled", "qned", "uhd", "lcd-tv",
                    " lcd tv", "smart tv",
                    "home entertain", "home_entertain", "home-entertain",
                    " hetv", "display")
    oven_terms   = ("oven", "range", "stove", "cooktop", "cookt", "cooking",
                    "rangetop", "induction", "wall_oven", "wall oven")
    av_terms     = ("audio", "soundbar", "sound bar", "speaker", " av ",
                    "home theater")
    ac_terms     = ("air conditioner", "aircond", "hvac", "ac unit",
                    "split ac", "rac", "ras", "split-ac")

    for k in refrig_terms:
        if k in c: return "refrigerator"
    for k in dish_terms:
        if k in c: return "dishwasher"
    for k in washer_terms:
        if k in c: return "washer"
    for k in dryer_terms:
        if k in c: return "dryer"
    for k in micro_terms:
        if k in c: return "microwave"
    for k in monitor_terms:
        if k in c: return "monitor"
    for k in tv_terms:
        if k in c: return "tv"
    for k in oven_terms:
        if k in c: return "oven"
    for k in av_terms:
        if k in c: return "av"
    for k in ac_terms:
        if k in c: return "ac"

    # ── LG ERP short codes (exact match on trimmed string) ──
    code_map = {
        "h/a": "refrigerator", "ha": "refrigerator",
        "he": "tv", "av": "av", "as": "ac", "rac": "ac",
        "ref": "refrigerator", "rfg": "refrigerator",
        "wm": "washer", "dr": "dryer", "dw": "dishwasher",
        "mw": "microwave", "ov": "oven", "wo": "oven",
        "tv": "tv", "mn": "monitor",
    }
    if c in code_map:
        return code_map[c]
    first = c.split("-")[0].split("_")[0].split(" ")[0]
    if first in code_map:
        return code_map[first]

    return "other"


def audit_unmatched(
    placements: list,
    master: dict,
) -> dict:
    """Return a structured diagnostic of which raw Division names fell to "other".

    Output schema::

        { "total_other_units": 16,
          "by_division": [
              {"division": "Cooking-Range", "count": 9,
               "samples": ["KIM-COOK-001", "KIM-COOK-014", …]},
              {"division": "<MISSING>", "count": 3, "samples": [...]},
              …
          ] }
    """
    unmatched: Dict[str, list] = {}
    for p in placements:
        mc = p.get("model_code", "")
        raw = (master.get(mc, {}) or {}).get("category", "") or ""
        if broad_category(raw) == "other":
            key = raw.strip() or "<MISSING>"
            unmatched.setdefault(key, []).append(mc)
    by_div = [
        {"division": k, "count": len(v), "samples": sorted(set(v))[:3]}
        for k, v in sorted(unmatched.items(), key=lambda kv: -len(kv[1]))
    ]
    return {
        "total_other_units": sum(len(v) for v in unmatched.values()),
        "by_division": by_div,
    }
