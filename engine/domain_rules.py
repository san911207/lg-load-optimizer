"""
Domain rules for LG appliance truck loading.
============================================

These rules encode warehouse-OPS preferences that go beyond pure geometric
bin-packing. They are applied at three stages:

    1. **Pre-pack** (this module): order/group items so pair-mates stay
       adjacent in the packing sequence (washer + dryer, stacked tower
       columns, side panels with their parent appliance).
    2. **Pack-time** (best_packer.py): heavy-bottom, door-track region,
       no-rotation, full-footprint stacking.
    3. **Post-pack** (this module): verify nothing violates fragile /
       this-side-up / category-separation rules.  Each violation comes
       with a Severity so the UI can warn vs. block.

Rules implemented today (Day 4):
    - Pair detection: washer+dryer with matching footprint → pack together.
    - Fragile no-overhead: items flagged fragile cannot have anything
      stacked above them.
    - Category separation: a hard list of (cat_above, cat_below) pairs
      that the warehouse rejects (e.g. microwave on top of fridge).

Day 5 extension (LIFO):
    - Will read ``stop_seq`` per order line. Items destined for stop #1
      (first delivery) load LAST (toward the rear / door), stop #N items
      load FIRST (toward the cab / front). LIFO is a packing-order
      constraint and integrates into the SA permutation space.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ── Rule definitions ────────────────────────────────────────────────────

# (above_category, below_category) pairs that the warehouse rejects.
# Reasoning: small light boxes on top of heavy machinery are unstable; the
# 'Panel' category is decorative trim that must never carry weight.
CATEGORY_BLACKLIST: Dict[Tuple[str, str], str] = {
    ("Microwave", "Refrigerator"): "Microwave too small to span fridge top — slips during transit.",
    ("Microwave", "Washer"): "Microwave shouldn't rest on appliance vents.",
    ("Cooktop", "Refrigerator"): "Cooktop glass-top must not have anything resting on it.",
    ("Panel", "Refrigerator"): "Decorative panels cannot bear weight from above.",
    ("Panel", "Washer"): "Decorative panels cannot bear weight from above.",
}

# Categories that share footprint and pair up at the warehouse.  Order
# matters: dryer sits on top of washer because the washer is heavier and
# has the rigid drum suitable for load-bearing.
PAIR_RULES: List[Tuple[str, str]] = [
    ("Washer", "Dryer"),     # dryer rests on washer
]

FOOTPRINT_TOLERANCE_IN = 1.5   # SKUs within this footprint diff are mate-eligible


class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


@dataclass
class Finding:
    rule: str
    severity: Severity
    message: str
    seq_above: Optional[int] = None
    seq_below: Optional[int] = None


# ── Pair detection (pre-pack) ──────────────────────────────────────────


def detect_pairs(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
) -> List[Tuple[str, str, int]]:
    """
    Identify washer+dryer pairs in the order.

    Returns a list of ``(washer_sku, dryer_sku, count)`` triples.  ``count``
    is the number of (washer, dryer) co-shipping pairs we can form — the
    minimum of the two quantities when their footprints match within
    ``FOOTPRINT_TOLERANCE_IN``.

    Pairs that survive this filter get a "co-locate" hint when SA perturbs
    the order so the dryer ends up directly on top of the washer.
    """
    pairs: List[Tuple[str, str, int]] = []
    qty_by_cat: Dict[str, List[Tuple[str, int, Dict[str, Any]]]] = {}
    for ol in order_lines:
        mc = ol["model_code"]
        if mc not in master:
            continue
        cat = master[mc].get("category", "")
        qty_by_cat.setdefault(cat, []).append((mc, int(ol["quantity"]), master[mc]))

    for upper_cat, lower_cat_or_below in PAIR_RULES:
        # Pair rule format: (dryer_category, washer_category) — but our list is
        # (washer, dryer). The "above" is dryer, the "below" is washer.
        below_cat, above_cat = upper_cat, lower_cat_or_below
        uppers = qty_by_cat.get(above_cat, [])
        lowers = qty_by_cat.get(below_cat, [])
        for u_mc, u_qty, u_spec in uppers:
            for l_mc, l_qty, l_spec in lowers:
                if _footprint_matches(u_spec, l_spec):
                    pairs.append((l_mc, u_mc, min(u_qty, l_qty)))
    return pairs


def _footprint_matches(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Two SKUs share a footprint if width AND depth match within tolerance."""
    return (
        abs(a.get("width_in", 0) - b.get("width_in", 0)) <= FOOTPRINT_TOLERANCE_IN
        and abs(a.get("depth_in", 0) - b.get("depth_in", 0)) <= FOOTPRINT_TOLERANCE_IN
    )


def expand_with_pair_hint(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Reorder ``order_lines`` so paired items appear adjacent.  Heuristic
    extreme-point packing visits items in input order, so adjacency in the
    list → adjacency in the truck.

    Algorithm: walk the original list. When a washer is encountered,
    immediately follow it with its dryer mate (if any qty left). The dryer
    gets pulled forward from its later position. Items with no pair are
    untouched.
    """
    pairs = detect_pairs(order_lines, master)
    if not pairs:
        return list(order_lines)

    # Build a remaining-qty map; mutate as we emit pair groups.
    qty_left: Dict[str, int] = {ol["model_code"]: int(ol["quantity"]) for ol in order_lines}

    # Quick lookup: for each washer SKU, which dryer SKUs are valid mates?
    mates: Dict[str, List[str]] = {}
    for w, d, _ in pairs:
        mates.setdefault(w, []).append(d)

    out: List[Dict[str, Any]] = []
    seen: set = set()
    for ol in order_lines:
        mc = ol["model_code"]
        if mc in seen:
            continue
        if qty_left.get(mc, 0) <= 0:
            seen.add(mc)
            continue

        if mc in mates:
            # emit washer + adjacent dryer pairs first, then any orphans of either.
            for dryer_mc in mates[mc]:
                pair_qty = min(qty_left[mc], qty_left.get(dryer_mc, 0))
                if pair_qty > 0:
                    out.append({"model_code": mc, "quantity": pair_qty})
                    out.append({"model_code": dryer_mc, "quantity": pair_qty})
                    qty_left[mc] -= pair_qty
                    qty_left[dryer_mc] -= pair_qty
            if qty_left.get(mc, 0) > 0:
                out.append({"model_code": mc, "quantity": qty_left[mc]})
                qty_left[mc] = 0
            seen.add(mc)
        else:
            # not a pair-eligible SKU — emit untouched
            if qty_left[mc] > 0:
                out.append({"model_code": mc, "quantity": qty_left[mc]})
                qty_left[mc] = 0
            seen.add(mc)

    # Emit any unaccounted-for items (dryers that didn't appear above).
    for ol in order_lines:
        mc = ol["model_code"]
        if qty_left.get(mc, 0) > 0:
            out.append({"model_code": mc, "quantity": qty_left[mc]})
            qty_left[mc] = 0
    return out


# ── Post-pack verification ─────────────────────────────────────────────


def verify(
    placements: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
) -> List[Finding]:
    """
    Walk the final placements and emit findings for every domain-rule
    violation.  The UI consumes these — `WARN` shows a yellow callout in
    the work order, `BLOCK` refuses to export the PDF.
    """
    findings: List[Finding] = []

    # Build z-stacking adjacency: for each placement, what is directly on top?
    above_of: Dict[int, List[Dict[str, Any]]] = {p["seq"]: [] for p in placements}
    EPS = 0.5
    for upper in placements:
        for lower in placements:
            if upper["seq"] == lower["seq"]:
                continue
            same_xy = (
                abs(upper["x_in"] - lower["x_in"]) <= EPS
                and abs(upper["y_in"] - lower["y_in"]) <= EPS
            )
            stacked = abs(upper["z_in"] - (lower["z_in"] + lower["dim_z_in"])) <= EPS
            if same_xy and stacked:
                above_of[lower["seq"]].append(upper)

    for p in placements:
        spec = master.get(p["model_code"], {})
        cat = spec.get("category", "")
        is_fragile = bool(spec.get("fragile", False))
        # ``fragile AND stackable`` means it's a packaged appliance/TV/monitor
        # whose carton is rated for stacking — boxes on top are allowed.
        # ``fragile AND NOT stackable`` is the real "nothing-above" case
        # (delicate items, glass panels, finished gas range tops, etc.).
        is_no_overhead = is_fragile and not bool(spec.get("stackable", False))

        # Fragile no-overhead
        if is_no_overhead and above_of[p["seq"]]:
            for q in above_of[p["seq"]]:
                findings.append(Finding(
                    rule="fragile_no_overhead",
                    severity=Severity.BLOCK,
                    message=f"{q['model_code']} stacked on fragile {p['model_code']} — relocate above item.",
                    seq_above=q["seq"], seq_below=p["seq"],
                ))

        # Category blacklist
        for q in above_of[p["seq"]]:
            q_cat = master.get(q["model_code"], {}).get("category", "")
            if (q_cat, cat) in CATEGORY_BLACKLIST:
                findings.append(Finding(
                    rule="category_blacklist",
                    severity=Severity.WARN,
                    message=CATEGORY_BLACKLIST[(q_cat, cat)],
                    seq_above=q["seq"], seq_below=p["seq"],
                ))

    return findings
