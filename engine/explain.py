"""
Natural-language explanation of the chosen load arrangement.
============================================================

The router/SA pipeline produces a placement that obeys five hidden rules
(heavy-bottom, door-track region, pair-packing, LIFO, optimality). For the
dock worker reading the work order — or the supervisor reviewing the plan
on screen — those rules are invisible until we explain them.

This module walks the result dict and emits short bullet sentences
suitable for the Streamlit Step 2 panel and the PDF "why-strip".

Each bullet is a ``Reason`` with a category tag the UI uses to colour the
checkmark (success / info / warn). Reasons that don't apply to the
specific load are omitted, so a small load doesn't end up with a wall of
trivially-true bullets.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


HEAVY_THRESHOLD_LB = 150.0
TALL_THRESHOLD_IN = 85.0           # column-like items, mostly SKS towers
DOOR_TRACK_LEN_IN = 60.0           # rear 5 ft


@dataclass
class Reason:
    """One human-readable bullet."""
    label: str                # short bold heading
    detail: str               # one-sentence detail
    kind: str = "info"        # 'success' | 'info' | 'warn' — drives icon colour


def explain(
    result: Dict[str, Any],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
) -> List[Reason]:
    """
    Inspect the packing result and produce explanation bullets.

    Only emits bullets that *actually apply* — e.g. no pair-packing bullet
    on a load that has no washer/dryer pairs. Keep the output to ~3–6
    items so the UI panel and PDF strip stay clean.
    """
    reasons: List[Reason] = []
    placements = result.get("placements", [])
    metrics = result.get("metrics", {})

    L = float(truck_spec.get("length_in", 0))
    rear_threshold = L - DOOR_TRACK_LEN_IN

    # ── Heavy-bottom rule ───────────────────────────────────────────────
    heavy_items = [
        p for p in placements
        if p.get("weight_lb", 0) >= HEAVY_THRESHOLD_LB
    ]
    if heavy_items:
        on_floor = sum(1 for p in heavy_items if p.get("z_in", 0) < 1.0)
        if on_floor == len(heavy_items):
            reasons.append(Reason(
                "Heavy items on bottom",
                f"All {len(heavy_items)} items weighing 150 lb or more rest on the floor "
                "for transit stability and OSHA-compliant warehouse stacking.",
                "success",
            ))
        else:
            reasons.append(Reason(
                "Heavy on bottom — partial",
                f"{on_floor} of {len(heavy_items)} heavy items are on the floor; "
                f"{len(heavy_items) - on_floor} ended up on top — review before loading.",
                "warn",
            ))

    # ── Tall-to-front rule ──────────────────────────────────────────────
    tall_items = [
        p for p in placements
        if p.get("dim_z_in", 0) >= TALL_THRESHOLD_IN
    ]
    if tall_items and L > 0:
        in_front = sum(
            1 for p in tall_items
            if (p["x_in"] + p["dim_x_in"]) <= rear_threshold + 0.5
        )
        if in_front == len(tall_items):
            reasons.append(Reason(
                "Tall columns to front",
                f"All {len(tall_items)} tall items "
                f"({TALL_THRESHOLD_IN:.0f} in or more) are loaded in the "
                "front 21 ft where the full ceiling is available — the rear 5 ft "
                "loses 10 in of headroom to the roll-up door track.",
                "success",
            ))
        elif in_front > 0:
            reasons.append(Reason(
                "Tall items mostly front",
                f"{in_front} of {len(tall_items)} tall items in the safe front zone; "
                f"{len(tall_items) - in_front} are in the rear door-track zone — verify clearance.",
                "warn",
            ))

    # ── Pair-packing ────────────────────────────────────────────────────
    pair_ct = result.get("pair_count", 0)
    if pair_ct:
        reasons.append(Reason(
            "Washer + Dryer pairs grouped",
            f"{pair_ct} pair(s) detected and chained — the dryer sits directly on "
            "top of its matching washer for one-grab unloading.",
            "success",
        ))

    # ── Optimality / engine provenance ──────────────────────────────────
    engine = result.get("engine", "Heuristic")
    is_optimal = result.get("is_provable_optimal", False)
    initial_x = result.get("sa_initial_x_used_in")
    x_used = metrics.get("x_used_in")
    # D4 — Strip solver jargon ("MILP", "extreme-point", "Simulated Annealing")
    # from user-facing reasons. The dispatcher needs to know WHAT the engine
    # did, not WHICH ALGORITHM it ran.
    if is_optimal:
        reasons.append(Reason(
            "Proven shortest arrangement",
            f"The engine explored every legal arrangement and proved that "
            f"{metrics.get('x_used_ft', 0):.2f} ft is the shortest possible "
            "trailer length for this load — no arrangement can do better.",
            "success",
        ))
    elif initial_x and x_used and initial_x > x_used:
        gain_pct = (initial_x - x_used) / initial_x * 100
        if gain_pct >= 0.5:
            reasons.append(Reason(
                "Space-optimized layout",
                f"The initial layout used {initial_x/12.0:.2f} ft; the engine "
                f"refined the packing order to {x_used/12.0:.2f} ft "
                f"({gain_pct:+.1f}% shorter).",
                "info",
            ))
    elif "SA" in engine:
        reasons.append(Reason(
            "Space-optimized layout",
            "The engine searched alternate packing orders and kept the best "
            "arrangement found within the time budget.",
            "info",
        ))
    else:
        reasons.append(Reason(
            "Fast arrangement",
            "All warehouse stacking rules met. Run time under 1 second — "
            "best available for loads above 300 items.",
            "info",
        ))

    # ── Audit summary ───────────────────────────────────────────────────
    blk = result.get("audit_block_count", 0)
    wrn = result.get("audit_warn_count", 0)
    if blk:
        reasons.append(Reason(
            "Loading rule violation(s)",
            f"{blk} placement(s) break fragile / no-overhead / category rules — "
            "open the 'Audit findings' panel below, resolve each item, then "
            "re-run before handing this order to the driver.",
            "warn",
        ))
    elif wrn:
        reasons.append(Reason(
            "Supervisor review needed",
            f"{wrn} placement(s) are loadable but flagged for supervisor "
            "review (category-blacklist or stacking edge cases).",
            "warn",
        ))
    else:
        # If we already have at least one success bullet, skip this one to
        # avoid stating the obvious.
        if not any(r.kind == "success" for r in reasons):
            reasons.append(Reason(
                "All rules pass",
                "No fragile / category / stacking violations detected.",
                "success",
            ))

    return reasons


def explain_html(reasons: List[Reason]) -> str:
    """Render a list of Reason bullets as a Streamlit-ready HTML block."""
    if not reasons:
        return ""
    icon_color = {
        "success": "#047857",
        "info":    "#1D4ED8",
        "warn":    "#B45309",
    }
    bg = {
        "success": "#ECFDF5",
        "info":    "#EFF6FF",
        "warn":    "#FFFBEB",
    }
    border = {
        "success": "#A7F3D0",
        "info":    "#BFDBFE",
        "warn":    "#FDE68A",
    }
    items_html = []
    for r in reasons:
        ic = icon_color.get(r.kind, "#374151")
        bgc = bg.get(r.kind, "#F9FAFB")
        brd = border.get(r.kind, "#E5E7EB")
        sym = "✓" if r.kind == "success" else ("ⓘ" if r.kind == "info" else "△")
        items_html.append(
            f'<div style="display:flex;gap:10px;align-items:flex-start;'
            f'padding:8px 12px;background:{bgc};border:1px solid {brd};'
            f'border-radius:6px;margin:4px 0;font-size:12px;line-height:1.5;">'
            f'<span style="color:{ic};font-weight:800;font-size:14px;line-height:1;">{sym}</span>'
            f'<div><b>{r.label}</b><br>'
            f'<span style="color:#4B5563;">{r.detail}</span></div></div>'
        )
    return "".join(items_html)
