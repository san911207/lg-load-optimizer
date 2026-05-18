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


def _broad(cat: str) -> str:
    """Local copy of app.broad_category — kept here to avoid circular import."""
    c = (cat or "").lower().strip()
    if not c:
        return "other"
    if "냉장고" in cat: return "refrigerator"
    if "세탁기" in cat: return "washer"
    if "건조기" in cat: return "dryer"
    if "식기" in cat:   return "dishwasher"
    if "전자레인지" in cat or "전자렌지" in cat: return "microwave"
    if "오븐" in cat or "쿡탑" in cat or "렌지" in cat or "레인지" in cat: return "oven"
    if "모니터" in cat: return "monitor"
    for key in ("refrigerator", "fridge", "dishwasher", "microwave", "monitor", "washer", "washing"):
        if key in c:
            if key in ("washing",): return "washer"
            if key == "fridge":     return "refrigerator"
            return key
    if "dryer" in c or "laundry-d" in c:           return "dryer"
    if "oven" in c or "range" in c or "stove" in c: return "oven"
    if c.startswith("tv") or "television" in c or c == "he": return "tv"
    return "other"


def aggregate_zones(
    placements: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    pair_count: int = 0,
) -> List[Zone]:
    """
    Group placements into Zones for the 3D + breakdown table.

    Grouping key = (broad_category, dim_x_in, dim_y_in, dim_z_in). Items
    sharing a footprint AND category collapse into one zone.

    Washer + Dryer pairs detected by ``pair_count`` are merged into a
    single "paired" zone so the breakdown table shows "B · Washer + Dryer
    (paired) — 8+8 — 3 rows × 3 lanes × 2 tiers".
    """
    # Step 1: per-item canonical category (via master)
    enriched: List[Dict[str, Any]] = []
    for p in placements:
        cat = master.get(p["model_code"], {}).get("category", "") if master else p.get("category", "")
        enriched.append({**p, "_broad": _broad(cat)})

    # Step 2: group by (broad, footprint)
    groups: Dict[Tuple[str, float, float, float], List[Dict[str, Any]]] = {}
    for p in enriched:
        key = (p["_broad"], p["dim_x_in"], p["dim_y_in"], p["dim_z_in"])
        groups.setdefault(key, []).append(p)

    # Step 3: sort groups by minimum x (cab → dock)
    sorted_groups = sorted(groups.items(), key=lambda kv: min(p["x_in"] for p in kv[1]))

    # Step 4: collapse adjacent (washer, dryer) pairs at the same footprint
    # if pair_count > 0.
    zones: List[Zone] = []
    used_indices: set = set()
    letters = "ABCDEFGHIJKL"

    def _layout_of(items: List[Dict[str, Any]]) -> Tuple[int, int, int, str]:
        rows = len({round(p["x_in"], 1) for p in items})
        lanes = len({round(p["y_in"], 1) for p in items})
        tiers = len({round(p["z_in"], 1) for p in items})
        return rows, lanes, tiers, f"{rows} rows × {lanes} lanes × {tiers} tier{'s' if tiers > 1 else ''}"

    z_idx = 0
    for i, ((broad, dx, dy, dz), items) in enumerate(sorted_groups):
        if i in used_indices:
            continue

        # Pair detection — if this is Washer and next group is Dryer at
        # adjacent x range and matching footprint, merge.
        paired = None
        if broad == "washer" and pair_count > 0:
            for j, ((b2, dx2, dy2, dz2), items2) in enumerate(sorted_groups):
                if j == i or j in used_indices:
                    continue
                if b2 != "dryer":
                    continue
                if abs(dx2 - dx) > 1 or abs(dy2 - dy) > 1:
                    continue
                # adjacency check: shares at least one x value
                xs1 = {round(p["x_in"], 0) for p in items}
                xs2 = {round(p["x_in"], 0) for p in items2}
                if xs1 & xs2:
                    paired = (j, items2)
                    break

        glyph = KOREAN_ZONE_GLYPH.get(broad, "기")
        kr = KOREAN_ZONE_LABEL.get(broad, "기타")

        if paired is not None:
            j, items2 = paired
            used_indices.add(j)
            combined = items + items2
            rows, lanes, tiers, layout_str = _layout_of(combined)
            avg_unit_wt = int(round(sum(p["weight_lb"] for p in combined) / max(len(combined), 1)))
            zone = Zone(
                zone_id=letters[z_idx],
                broad_category="washer_dryer_pair",
                kr_label="세탁기 + 건조기 (페어)",
                kr_glyph="세건",
                item_count=len(combined),
                layout=layout_str,
                rows=rows, lanes=lanes, tiers=tiers,
                length_ft_start=round(min(p["x_in"] for p in combined) / 12.0, 1),
                length_ft_end=round(max(p["x_in"] + p["dim_x_in"] for p in combined) / 12.0, 1),
                weight_lb=int(round(sum(p["weight_lb"] for p in combined))),
                unit_weight_lb=avg_unit_wt,
                item_seqs=[p.get("seq", 0) for p in combined],
                is_pair=True,
            )
        else:
            rows, lanes, tiers, layout_str = _layout_of(items)
            unit_wt = int(round(items[0]["weight_lb"])) if items else 0
            zone = Zone(
                zone_id=letters[z_idx],
                broad_category=broad,
                kr_label=kr,
                kr_glyph=glyph,
                item_count=len(items),
                layout=layout_str,
                rows=rows, lanes=lanes, tiers=tiers,
                length_ft_start=round(min(p["x_in"] for p in items) / 12.0, 1),
                length_ft_end=round(max(p["x_in"] + p["dim_x_in"] for p in items) / 12.0, 1),
                weight_lb=int(round(sum(p["weight_lb"] for p in items))),
                unit_weight_lb=unit_wt,
                item_seqs=[p.get("seq", 0) for p in items],
                is_pair=False,
            )
        zones.append(zone)
        z_idx += 1
        used_indices.add(i)

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
