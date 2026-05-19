"""
Zone aggregator — turn flat placements into Zone × Stage data structures.

The CEO's target PDF design (2026-05-18, see docs/v2-mockups/target_pdf_*)
shows the load as a small number of Zones (Refrigerator, Washer+Dryer
paired, Dishwasher, Wall Oven) with rows × lanes × tiers per zone, then
decomposes the load into 5 macro stages for the side-view "장입 5단계"
cards. Both the Streamlit Step-2 page and the PDF v4 work-order print
the same data — so the aggregation lives here once.

Zone vs Stage:
  Zone   = group of items sharing the same broad category AND footprint
           (e.g. all Refrigerators with 26×26 footprint). Tells the
           DISPATCHER "what got loaded where".
  Stage  = ordered loading step a worker actually performs. Typically
           5 stages — Refrigerator → Washer (floor) → Dryer (stack) →
           Dishwasher → Wall Oven + close-out. Tells the WORKER
           "load this batch next".

Stages = ordered zones with pair-detection collapsed into a single
"Washer + Dryer paired" zone for the 5-stage cards.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


KOREAN_ZONE_GLYPH: Dict[str, str] = {
    # broad_category → single Hangul glyph (Field Veteran fix #6)
    "refrigerator": "냉",
    "washer":       "세",
    "dryer":        "건",
    "dishwasher":   "식",
    "microwave":    "전",
    "oven":         "오",
    "tv":           "TV",
    "monitor":      "모",
    "av":           "AV",
    "other":        "기",
}

KOREAN_ZONE_LABEL: Dict[str, str] = {
    "refrigerator": "냉장고",
    "washer":       "세탁기",
    "dryer":        "건조기",
    "dishwasher":   "식기세척기",
    "microwave":    "전자레인지",
    "oven":         "오븐 / 레인지",
    "tv":           "TV",
    "monitor":      "모니터",
    "av":           "오디오",
    "other":        "기타",
}


@dataclass
class Zone:
    """A homogeneous batch of items (same broad category, same footprint)."""
    zone_id: str                          # "A", "B", "C", …
    broad_category: str                   # "refrigerator", "washer", …
    kr_label: str                         # "냉장고"
    kr_glyph: str                         # "냉"
    item_count: int
    layout: str                           # "3 rows × 2 lanes × 1 tier"
    rows: int
    lanes: int
    tiers: int
    length_ft_start: float
    length_ft_end: float
    weight_lb: int
    unit_weight_lb: int
    item_seqs: List[int] = field(default_factory=list)
    is_pair: bool = False                  # washer+dryer chained
    # When broad_category resolves to "other", carry the most-frequent
    # raw Division-name value so the UI can show it instead of a generic
    # "Other" — gives the dispatcher a clue about what wasn't matched.
    raw_category: str = ""


@dataclass
class Stage:
    """One worker-facing macro step in the 5-step loading sequence."""
    step_no: int                          # 1..5
    title_en: str                         # "Refrigerator"
    title_kr: str                         # "냉장고"
    units: int
    unit_weight_lb: int
    layout: str                           # "3 rows × 2 lanes × 1 tier"
    length_range_ft: str                  # "0 → 8.9 ft"
    crew: int                             # 1 or 2 (per Warehouse Veteran fix #7)
    estimated_min: int                    # estimated minutes (Field Veteran #5)
    cumulative_lift_lb_per_person: int    # accumulated 1-person lift weight
    safety_note: str                      # "⚠ 이거 안 하면..." (Field #8)
    instructions: List[str]               # short Korean bullet list
    zones: List[Zone] = field(default_factory=list)


HEAVY_PER_PERSON_LB = 150.0      # threshold above which 2-person crew required


from engine.categorizer import broad_category as _broad  # single source of truth


def _zone_letter(idx: int) -> str:
    """Return zone ID: A..Z, then AA, AB, … for overflow.

    Resilient against any number of zones — the original
    ``letters="ABCDEFGHIJKL"`` (12-char) cap crashed when an
    LG-ERP master with many distinct SKU footprints produced
    13+ zones (IndexError observed in build #22 .exe).
    """
    alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if idx < len(alpha):
        return alpha[idx]
    return alpha[idx // len(alpha) - 1] + alpha[idx % len(alpha)]


def aggregate_zones(
    placements: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    pair_count: int = 0,
) -> List[Zone]:
    """
    Group placements into Zones for the 3D + breakdown table.

    Strategy (matches CEO target design 2026-05-18): group by
    ``broad_category`` ONLY. Footprint variance within a category collapses
    into aggregate layout dimensions (max rows × max lanes × max tiers).

    The earlier (broad, footprint) key produced 30+ zones on real LG-ERP
    masters where each SKU has slightly different dims; the table became
    unreadable and the .exe crashed when the zone letter pool ran out.

    Washer + Dryer is handled specially: when ``pair_count > 0`` the
    "washer" + "dryer" categories merge into one "washer_dryer_pair"
    zone so the breakdown shows ``B · Washer + Dryer (paired) — 8 + 8``.
    """
    # Step 1: per-item canonical broad category + remember raw cat
    enriched: List[Dict[str, Any]] = []
    for p in placements:
        cat = master.get(p["model_code"], {}).get("category", "") if master else p.get("category", "")
        enriched.append({**p, "_broad": _broad(cat), "_raw_cat": str(cat).strip()})

    # Step 2: group by broad category only
    groups: Dict[str, List[Dict[str, Any]]] = {}
    raw_freq: Dict[str, Dict[str, int]] = {}
    for p in enriched:
        groups.setdefault(p["_broad"], []).append(p)
        if p["_raw_cat"]:
            raw_freq.setdefault(p["_broad"], {})
            raw_freq[p["_broad"]][p["_raw_cat"]] = raw_freq[p["_broad"]].get(p["_raw_cat"], 0) + 1

    # Step 3: order categories cab → dock by min(x)
    def _min_x(items: List[Dict[str, Any]]) -> float:
        return min(p["x_in"] for p in items) if items else 0.0
    sorted_cats = sorted(groups.keys(), key=lambda k: _min_x(groups[k]))

    def _layout_of(items: List[Dict[str, Any]]) -> Tuple[int, int, int, str]:
        rows = len({round(p["x_in"], 1) for p in items})
        lanes = len({round(p["y_in"], 1) for p in items})
        tiers = len({round(p["z_in"], 1) for p in items})
        return rows, lanes, tiers, f"{rows} rows × {lanes} lanes × {tiers} tier{'s' if tiers > 1 else ''}"

    # Step 4: pair-collapse washer + dryer if both present
    pair_zone_made = False
    zones: List[Zone] = []
    handled: set = set()
    z_idx = 0

    if pair_count > 0 and "washer" in groups and "dryer" in groups:
        combined = groups["washer"] + groups["dryer"]
        rows, lanes, tiers, layout_str = _layout_of(combined)
        avg_unit_wt = int(round(sum(p["weight_lb"] for p in combined) / max(len(combined), 1)))
        zones.append(Zone(
            zone_id=_zone_letter(z_idx),
            broad_category="washer_dryer_pair",
            kr_label=KOREAN_ZONE_LABEL["washer"] + " + " + KOREAN_ZONE_LABEL["dryer"] + " (페어)",
            kr_glyph=KOREAN_ZONE_GLYPH["washer"] + KOREAN_ZONE_GLYPH["dryer"],
            item_count=len(combined),
            layout=layout_str,
            rows=rows, lanes=lanes, tiers=tiers,
            length_ft_start=round(min(p["x_in"] for p in combined) / 12.0, 1),
            length_ft_end=round(max(p["x_in"] + p["dim_x_in"] for p in combined) / 12.0, 1),
            weight_lb=int(round(sum(p["weight_lb"] for p in combined))),
            unit_weight_lb=avg_unit_wt,
            item_seqs=[p.get("seq", 0) for p in combined],
            is_pair=True,
        ))
        z_idx += 1
        handled.update({"washer", "dryer"})
        pair_zone_made = True

    # Step 5: emit one zone per remaining category, ordered by x position
    for broad in sorted_cats:
        if broad in handled:
            continue
        items = groups[broad]
        rows, lanes, tiers, layout_str = _layout_of(items)
        unit_wt = int(round(sum(p["weight_lb"] for p in items) / max(len(items), 1)))
        # Most-frequent raw Division-name for this broad bucket
        freq = raw_freq.get(broad, {})
        most_freq_raw = max(freq.items(), key=lambda kv: kv[1])[0] if freq else ""
        zones.append(Zone(
            zone_id=_zone_letter(z_idx),
            broad_category=broad,
            kr_label=KOREAN_ZONE_LABEL.get(broad, "기타"),
            kr_glyph=KOREAN_ZONE_GLYPH.get(broad, "기"),
            item_count=len(items),
            layout=layout_str,
            rows=rows, lanes=lanes, tiers=tiers,
            length_ft_start=round(min(p["x_in"] for p in items) / 12.0, 1),
            length_ft_end=round(max(p["x_in"] + p["dim_x_in"] for p in items) / 12.0, 1),
            weight_lb=int(round(sum(p["weight_lb"] for p in items))),
            unit_weight_lb=unit_wt,
            item_seqs=[p.get("seq", 0) for p in items],
            is_pair=False,
            raw_category=most_freq_raw,
        ))
        z_idx += 1

    # Sort zones by min-x so display order matches load order.
    zones.sort(key=lambda z: z.length_ft_start)
    # Reassign letters in display order
    for i, z in enumerate(zones):
        z.zone_id = _zone_letter(i)
    return zones


def stages_from_zones(zones: List[Zone]) -> List[Stage]:
    """
    Decompose zones into the 5-step worker-facing sequence.

    Pair zones (Washer + Dryer) split into TWO stages so the worker has
    one card for "Washer at floor" and one for "Dryer on top". Other
    zones map 1:1 to stages.

    Stage attributes computed:
      * crew     — 2P if avg unit weight ≥ HEAVY_PER_PERSON_LB else 1P
                   (Warehouse Veteran #2 fix — was uniformly 2P).
      * estimated_min — heuristic: 1.5 min/item × crew_factor
      * cumulative_lift_lb_per_person — running total across stages
      * safety_note — per-category catch from a static map
    """
    SAFETY: Dict[str, str] = {
        "refrigerator": "캡 벽에 라쳇 1차 고정. 2인 작업, 핸드트럭 필수.",
        "washer":       "Transit bolt 4개 체결 확인. 미체결 시 베어링 파손.",
        "dryer":        "세탁기 위에 정확히 정렬. 2인 lift.",
        "dishwasher":   "호스 측 위로. 잔수 누출 방지.",
        "microwave":    "유리문 모서리 보호. 가벼움.",
        "oven":         "도어 잠금 확인. 글래스탑 위 적재 금지.",
        "tv":           "Carton ↑ 화살표 방향 준수. 가로 적재 금지.",
        "monitor":      "패키지 상부 충격 주의.",
    }

    stages: List[Stage] = []
    cumulative_lift = 0
    step = 1
    for z in zones:
        if z.is_pair:
            # Split pair into 2 stages — washer first (floor), then dryer (stack)
            washer_units = z.item_count // 2
            dryer_units = z.item_count - washer_units
            wt_per = z.unit_weight_lb
            for cat_key, units, tier_note, kr_title in (
                ("washer", washer_units, "1 tier", "세탁기 (바닥)"),
                ("dryer",  dryer_units,  "tier 2", "건조기 (위 스택)"),
            ):
                crew = 2 if wt_per >= HEAVY_PER_PERSON_LB else 1
                if cat_key == "washer" and wt_per >= 80:
                    crew = 2   # washers (200lb+) always 2P
                if cat_key == "dryer" and wt_per >= 70:
                    crew = 2   # dryer onto washer (2P lift)
                est_min = max(2, int(units * 1.5 / max(crew, 1)))
                if crew == 1:
                    cumulative_lift += units * wt_per
                stages.append(Stage(
                    step_no=step,
                    title_en="Washer" if cat_key == "washer" else "Dryer",
                    title_kr=kr_title,
                    units=units,
                    unit_weight_lb=wt_per,
                    layout=f"{z.rows} rows × {z.lanes} lanes × {tier_note}",
                    length_range_ft=f"{z.length_ft_start} → {z.length_ft_end} ft",
                    crew=crew,
                    estimated_min=est_min,
                    cumulative_lift_lb_per_person=cumulative_lift,
                    safety_note=SAFETY.get(cat_key, ""),
                    instructions=[
                        "바닥에 가득 깔기. 다음 단계 stack 준비." if cat_key == "washer"
                        else "정확히 정렬 후 2인 lift."
                    ],
                    zones=[z],
                ))
                step += 1
        else:
            crew = 2 if z.unit_weight_lb >= HEAVY_PER_PERSON_LB else 1
            # Special: fridges always 2P regardless of unit weight
            if z.broad_category == "refrigerator":
                crew = 2
            est_min = max(2, int(z.item_count * 1.5 / max(crew, 1)))
            if crew == 1:
                cumulative_lift += z.item_count * z.unit_weight_lb
            stages.append(Stage(
                step_no=step,
                title_en=z.broad_category.capitalize(),
                title_kr=z.kr_label + (" + 마감" if z.broad_category == "oven" else ""),
                units=z.item_count,
                unit_weight_lb=z.unit_weight_lb,
                layout=z.layout,
                length_range_ft=f"{z.length_ft_start} → {z.length_ft_end} ft",
                crew=crew,
                estimated_min=est_min,
                cumulative_lift_lb_per_person=cumulative_lift,
                safety_note=SAFETY.get(z.broad_category, ""),
                instructions=[],
                zones=[z],
            ))
            step += 1

    return stages


# =============================================================================
# Row-based loading sequence (2026-05-19 — CEO Q4 direction)
# =============================================================================
# Stages above group by broad_category — useful for the dispatcher who needs
# to know "what got loaded where" but not faithful to the physical loading
# order a forklift operator must follow. A forklift can't drive past row 5 to
# place row 3, so the actual loading order is FRONT-to-REAR row-by-row.
#
# aggregate_rows() groups placements by their x_in column position, sorts
# front-to-rear, and emits one Row per distinct column. A row may be
# single-category (12 Microwaves stacked at the front wall) or mixed
# (2 Dryer + 1 Refrigerator + 2 Washer all at x=4.7 ft).
# =============================================================================


@dataclass
class LoadRow:
    """One physical row in the truck — placements sharing the same x_in column.

    The dispatcher reads stages_from_zones() for the section-level summary;
    the forklift operator reads aggregate_rows() for the literal load order.
    """
    row_no: int                          # 1-indexed front-to-rear
    x_in: float                          # column start in inches
    x_ft: float                          # column start in feet (display)
    x_end_in: float                      # column end (max x_in + dim_x_in)
    placements: List[Dict[str, Any]]
    units: int
    # broad_category → count, e.g. {"washer": 2, "dryer": 2, "refrigerator": 1}
    categories: Dict[str, int] = field(default_factory=dict)
    raw_categories: List[str] = field(default_factory=list)   # original ERP labels
    is_mixed: bool = False               # 2+ different broad_categories
    has_stack: bool = False              # 2+ tier levels in this row
    max_z_in: float = 0.0                # tallest item top in this row
    total_weight_lb: float = 0.0


def aggregate_rows(
    placements: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    x_tol_in: float = 4.0,
) -> List[LoadRow]:
    """Group placements into physical rows by x_in column (front-to-rear).

    Args:
        placements: raw placement list from the solver
        master: model_master dict for category lookup
        x_tol_in: cluster placements within this many inches of each other
                  as the same row (handles solver float drift on shared
                  columns; default 4 in = ~10 cm)

    Returns:
        Ordered list of LoadRow, row_no 1-indexed from FRONT.
    """
    from engine.categorizer import broad_category

    if not placements:
        return []

    # Sort by (x_in, z_in, y_in) so placements share-column-then-stack-then-lane
    sorted_p = sorted(placements, key=lambda p: (p["x_in"], p["z_in"], p["y_in"]))

    # Cluster by x_in within tolerance
    clusters: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    cur_anchor: Optional[float] = None
    for p in sorted_p:
        x = float(p["x_in"])
        if cur_anchor is None or abs(x - cur_anchor) <= x_tol_in:
            cur.append(p)
            cur_anchor = x if cur_anchor is None else cur_anchor
        else:
            clusters.append(cur)
            cur = [p]
            cur_anchor = x
    if cur:
        clusters.append(cur)

    rows: List[LoadRow] = []
    for i, ps in enumerate(clusters, start=1):
        x_in_min = min(p["x_in"] for p in ps)
        x_end = max(p["x_in"] + p.get("dim_x_in", 0) for p in ps)
        z_max = max(p["z_in"] + p.get("dim_z_in", 0) for p in ps)
        layers = {round(p["z_in"], 0) for p in ps}
        weight = sum(float(p.get("weight_lb", 0)) * 1 for p in ps)
        cats: Dict[str, int] = {}
        raw_cats: List[str] = []
        for p in ps:
            mc = p.get("model_code", "")
            spec = master.get(mc, {})
            raw_cat = spec.get("category", p.get("category", "other"))
            raw_cats.append(str(raw_cat))
            broad = broad_category(str(raw_cat))
            cats[broad] = cats.get(broad, 0) + 1
        rows.append(LoadRow(
            row_no=i,
            x_in=float(x_in_min),
            x_ft=round(x_in_min / 12.0, 1),
            x_end_in=float(x_end),
            placements=ps,
            units=len(ps),
            categories=cats,
            raw_categories=list(dict.fromkeys(raw_cats)),  # unique, order-preserving
            is_mixed=len(cats) > 1,
            has_stack=len(layers) > 1,
            max_z_in=float(z_max),
            total_weight_lb=round(weight, 1),
        ))
    return rows


# Pretty-print helper used by render_step2_v4 to summarise a row's contents
# in one short line, e.g. "2 Refrigerator + 1 Microwave" or "12 Microwave".
def row_summary(row: LoadRow) -> str:
    label = {
        "refrigerator": "Refrigerator",
        "washer": "Washer",
        "dryer": "Dryer",
        "washer_dryer_pair": "Washer+Dryer",
        "dishwasher": "Dishwasher",
        "microwave": "Microwave",
        "oven": "Oven",
        "tv": "TV",
        "monitor": "Monitor",
        "av": "Audio",
        "ac": "AC",
        "other": "Other",
    }
    parts = [
        f"{n} {label.get(broad, broad.capitalize())}"
        for broad, n in sorted(row.categories.items(), key=lambda kv: -kv[1])
    ]
    return " + ".join(parts) if parts else f"{row.units} units"
