"""
=============================================================================
Best-Pack Engine — LG Appliance Truck Loading Optimizer
=============================================================================

3D Bin-Packing with FIXED orientation (industry standard for appliances/TVs).

Units (US):
  - All linear dims in inches (in)
  - Weight in pounds (lb)
  - Volume in cubic feet (cft)
  - Door track loss = 10 inches at top of rear (last 5 ft)

Constraints:
  - Upright only (height vertical, z fixed)
  - **No horizontal rotation** — width and depth fixed per manufacturer spec
  - Stackable=False → item must be at z=0 (floor only)
  - Stackable=True → item can sit on top of any other placed box
  - Max-fit mode: load_bear_lb and fragile are ignored (CEO 2026-05-15)

Algorithm: Extreme-Point Heuristic
  - All items expanded individually
  - Tried under multiple sort orders, best result selected
  - For each item, candidate positions = extreme points (corners of placed
    boxes + origin). Pick the candidate that minimizes max-x (length used),
    breaking ties by lowest z then lowest y (ground-first, compact).
  - Stackable=False items skipped from z>0 candidates.
  - Boxes naturally stack across dim groups (microwave on top of fridge etc.)
    and last-row slots backfill from later items.
"""

from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass, field


DOOR_TRACK_LOSS_IN = 10     # 10" headroom loss at the rear roll-up door track
DOOR_TRACK_LEN_IN = 60.0    # Rear 5 ft where the door-track lowers the ceiling
IN_PER_FT = 12.0
CUIN_PER_CFT = 1728.0
EPS = 1e-6                  # float tolerance for collision/bounds


@dataclass
class Placement:
    """Single box placement in the truck (all dims in inches, weight in lb)."""
    seq: int
    model_code: str
    x: float          # length-wise (in, from cab)
    y: float          # width-wise (in)
    z: float          # vertical (in)
    dim_x: float      # depth along truck length
    dim_y: float      # width along truck width
    dim_z: float      # height
    weight_lb: float
    lane: int         # approx lane index = floor(y / dim_y)
    layer: int        # approx layer index = floor(z / dim_z)
    stackable: bool = True   # can support load on top (LG warehouse rule)


@dataclass
class PackResult:
    strategy: str
    placements: List[Placement]
    unfitted: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def fitted_count(self) -> int:
        return len(self.placements)

    @property
    def unfitted_count(self) -> int:
        return sum(u["quantity"] for u in self.unfitted)

    @property
    def x_used(self) -> float:
        return max((p.x + p.dim_x) for p in self.placements) if self.placements else 0.0

    @property
    def total_weight_lb(self) -> float:
        return sum(p.weight_lb for p in self.placements)

    def metrics(self, truck_spec: Dict[str, Any]) -> Dict[str, Any]:
        used_vol_cuin = sum(p.dim_x * p.dim_y * p.dim_z for p in self.placements)
        used_vol_cft = used_vol_cuin / CUIN_PER_CFT
        truck_vol_cuin = (
            truck_spec["length_in"] * truck_spec["width_in"] * truck_spec["height_in"]
        )
        truck_cargo_cft = truck_spec.get(
            "cargo_volume_cft", truck_vol_cuin / CUIN_PER_CFT
        )
        return {
            "strategy": self.strategy,
            "fitted_count": self.fitted_count,
            "unfitted_count": self.unfitted_count,
            "x_used_in": round(self.x_used, 2),
            "x_used_ft": round(self.x_used / IN_PER_FT, 2),
            "compactness_pct": round(self.x_used / truck_spec["length_in"] * 100, 2),
            "volume_loaded_cft": round(used_vol_cft, 2),
            "volume_util_pct": round(used_vol_cft / truck_cargo_cft * 100, 2),
            "weight_total_lb": round(self.total_weight_lb, 1),
            "weight_util_pct": round(
                self.total_weight_lb / truck_spec["max_payload_lb"] * 100, 2
            ),
            "remaining_length_in": round(truck_spec["length_in"] - self.x_used, 2),
            "remaining_length_ft": round(
                (truck_spec["length_in"] - self.x_used) / IN_PER_FT, 2
            ),
        }


# ─────────────────────────────────────────────────────────────────────────
# Extreme-Point Heuristic
# ─────────────────────────────────────────────────────────────────────────
def _collides(x: float, y: float, z: float, dx: float, dy: float, dz: float,
              placed: List[Placement]) -> bool:
    x1, y1, z1 = x + dx, y + dy, z + dz
    for p in placed:
        if (x + EPS < p.x + p.dim_x and x1 - EPS > p.x and
            y + EPS < p.y + p.dim_y and y1 - EPS > p.y and
            z + EPS < p.z + p.dim_z and z1 - EPS > p.z):
            return True
    return False


def _pack_with_strategy(
    items: List[Dict[str, Any]],
    L: float, W: float, H_full: float,
    strategy_name: str,
) -> PackResult:
    """
    H_full = full truck interior height (e.g. 97" for 26ft).
    Door-track penalty (10") only applies to the rear 5 ft of the truck
    (x > L - DOOR_TRACK_LEN_IN). Items placed entirely in the front region
    can use the full ceiling height. Items reaching into the rear region
    must fit under H_full - DOOR_TRACK_LOSS_IN.
    """
    placements: List[Placement] = []
    unfitted_by_model: Dict[str, int] = {}
    extreme_points: List[Tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
    H_rear = H_full - DOOR_TRACK_LOSS_IN  # effective ceiling in door-track zone
    rear_threshold = L - DOOR_TRACK_LEN_IN  # x position where door track begins

    for it in items:
        d, w, h = it["d"], it["w"], it["h"]
        stackable = it["stackable"]

        # Find best candidate position
        best = None  # tuple (new_max_x, z, y, x, ep_idx)
        for ep in extreme_points:
            ex, ey, ez = ep
            x1 = ex + d
            y1 = ey + w
            z1 = ez + h
            # Bounds — width/length always against truck
            if x1 > L + EPS or y1 > W + EPS:
                continue
            # Position-dependent ceiling: if any part of the box crosses into
            # the rear door-track region, the box must fit under H_rear (87").
            # Items fully in front (x1 <= rear_threshold) can use H_full (97").
            max_h_here = H_rear if x1 > rear_threshold + EPS else H_full
            if z1 > max_h_here + EPS:
                continue
            # Stackable constraint
            if not stackable and ez > EPS:
                continue
            # Collision check
            if _collides(ex, ey, ez, d, w, h, placements):
                continue
            # Support check: if z > 0, must rest on existing box(es)
            if ez > EPS and not _supported_from_below(ex, ey, ez, d, w, placements):
                continue
            # Score: minimize length used (max-x), then height, then width
            current_max_x = max((p.x + p.dim_x for p in placements), default=0.0)
            new_max_x = max(current_max_x, x1)
            score = (new_max_x, ez, ey)
            if best is None or score < best:
                best = (new_max_x, ez, ey, ex)

        if best is None:
            unfitted_by_model[it["model_code"]] = (
                unfitted_by_model.get(it["model_code"], 0) + 1
            )
            continue

        _, ez, ey, ex = best
        new_placement = Placement(
            seq=0,
            model_code=it["model_code"],
            x=ex, y=ey, z=ez,
            dim_x=d, dim_y=w, dim_z=h,
            weight_lb=it["weight"],
            lane=int(round(ey / w)) if w > 0 else 0,
            layer=int(round(ez / h)) if h > 0 else 0,
            stackable=stackable,
        )
        placements.append(new_placement)

        # Update extreme points: 3 new candidate corners
        new_eps = [
            (ex + d, ey, ez),   # right face origin
            (ex, ey + w, ez),   # +y face origin
            (ex, ey, ez + h),   # top face origin
        ]
        for new_ep in new_eps:
            nex, ney, nez = new_ep
            # Prune obviously bad EPs: out of bounds or inside an existing box
            if nex > L - EPS or ney > W - EPS or nez > H_full - EPS:
                continue
            if any(p.x + EPS < nex < p.x + p.dim_x - EPS and
                   p.y + EPS < ney < p.y + p.dim_y - EPS and
                   p.z + EPS < nez < p.z + p.dim_z - EPS
                   for p in placements):
                continue
            if new_ep not in extreme_points:
                extreme_points.append(new_ep)
        # Remove the consumed EP if it was used
        # (Optional optimization; left in is fine — collision check filters later)

    for i, p in enumerate(placements, 1):
        p.seq = i

    unfitted = [
        {"model_code": mc, "quantity": q} for mc, q in unfitted_by_model.items()
    ]
    return PackResult(strategy=strategy_name, placements=placements, unfitted=unfitted)


def _supported_from_below(
    x: float, y: float, z: float, d: float, w: float,
    placed: List[Placement],
) -> bool:
    """Strict support — the new box's entire footprint must rest on ONE box
    whose top face is at z. No overhang allowed (would look like the box is
    floating + would be physically unsafe to load).

    The supporter must also have ``stackable=True``. Fridges, ranges, and
    wall-ovens carry ``stackable=False`` because their tops are not
    engineered to hold load — placing items on them violates warehouse-OPS
    rules and the post-pack audit will BLOCK the work order otherwise.
    """
    if z <= EPS:
        return True  # floor
    x1, y1 = x + d, y + w
    for p in placed:
        # Top face at exactly this z?
        if abs((p.z + p.dim_z) - z) > EPS:
            continue
        # Supporter must FULLY contain the new box's footprint
        if (p.x <= x + EPS and
            p.x + p.dim_x >= x1 - EPS and
            p.y <= y + EPS and
            p.y + p.dim_y >= y1 - EPS):
            # Supporter must be stackable on top
            if not p.stackable:
                continue
            return True
    return False


def _expand_items(
    order_lines: List[Dict[str, Any]], master: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Expand order lines into individual item records."""
    items: List[Dict[str, Any]] = []
    for ol in order_lines:
        spec = master[ol["model_code"]]
        for _ in range(ol["quantity"]):
            items.append({
                "model_code": ol["model_code"],
                "w": spec["width_in"],
                "d": spec["depth_in"],
                "h": spec["height_in"],
                "weight": spec["weight_lb"],
                "stackable": bool(spec.get("stackable", False)),
            })
    return items


def find_best(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
) -> PackResult:
    """Try multiple sort orders, pick result with most fitted then shortest length."""
    L = truck_spec["length_in"]
    W = truck_spec["width_in"]
    # Pass FULL interior height — the packer applies door-track penalty
    # only to items reaching the rear 5 ft (position-dependent ceiling).
    H = truck_spec["height_in"]

    base_items = _expand_items(order_lines, master)

    strategies = [
        ("height_desc",     lambda i: (-i["h"], -i["w"] * i["d"], i["model_code"])),
        ("volume_desc",     lambda i: (-i["w"] * i["d"] * i["h"], i["model_code"])),
        ("base_area_desc",  lambda i: (-i["w"] * i["d"], -i["h"], i["model_code"])),
        ("depth_desc",      lambda i: (-i["d"], -i["w"], i["model_code"])),
    ]

    results: List[PackResult] = []
    for name, key in strategies:
        sorted_items = sorted(base_items, key=key)
        results.append(_pack_with_strategy(sorted_items, L, W, H, name))

    # Best = most fitted, then min length
    results.sort(key=lambda r: (-r.fitted_count, r.x_used))
    return results[0]


def fits_formula(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Closed-form UPPER BOUND predictor — per-group sequential packing with
    fixed orientation. Real simulator (`simulate()`) uses extreme-point heuristic
    and may give shorter length via cross-group stacking / row backfill.

    Formula (per dim-group g):
        eff_H    = H_truck − 10 in
        layers_g = ⌊eff_H / h_g⌋ if stackable_g else 1
        lanes_g  = ⌊W_truck / w_g⌋
        per_row  = lanes_g × layers_g
        rows_g   = ⌈Q_g / per_row⌉
        length_g = rows_g × d_g

    FITS_upper iff Σ length_g ≤ L_truck.

    All linear units in inches. No rotation. No cross-group optimization in
    this formula (use simulate() for the actual best).
    """
    eff_h = truck_spec["height_in"] - DOOR_TRACK_LOSS_IN
    truck_w = truck_spec["width_in"]
    truck_l = truck_spec["length_in"]

    groups: Dict[Tuple[float, float, float], Dict[str, Any]] = {}
    for ol in order_lines:
        spec = master[ol["model_code"]]
        key = (spec["width_in"], spec["depth_in"], spec["height_in"])
        if key not in groups:
            groups[key] = {"spec": spec, "qty": 0, "models": []}
        groups[key]["qty"] += ol["quantity"]
        groups[key]["models"].append((ol["model_code"], ol["quantity"]))

    total_length: float = 0.0
    breakdown: List[Dict[str, Any]] = []
    for key, g in groups.items():
        w, d, h = key
        stackable = bool(g["spec"].get("stackable", False))
        layers = max(1, int(eff_h // h)) if stackable else 1
        lanes = max(1, int(truck_w // w))
        per_row = lanes * layers
        rows = -(-g["qty"] // per_row)
        length = rows * d
        total_length += length
        breakdown.append({
            "models": g["models"],
            "qty": g["qty"],
            "w_in": w, "d_in": d, "h_in": h,
            "stackable": stackable,
            "lanes": lanes,
            "layers": layers,
            "per_row": per_row,
            "rows": rows,
            "length_in": round(length, 2),
            "length_ft": round(length / IN_PER_FT, 2),
        })

    return {
        "fits": total_length <= truck_l,
        "predicted_length_in": round(total_length, 2),
        "predicted_length_ft": round(total_length / IN_PER_FT, 2),
        "truck_length_in": truck_l,
        "truck_length_ft": round(truck_l / IN_PER_FT, 2),
        "remaining_in": round(truck_l - total_length, 2),
        "remaining_ft": round((truck_l - total_length) / IN_PER_FT, 2),
        "effective_height_in": eff_h,
        "breakdown": breakdown,
    }


def simulate(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Main entrypoint — true 3D bin-packing with fixed orientation.

    Returns simulation report with metrics + placements. All linear units in
    inches, weight in pounds.
    """
    best = find_best(order_lines, master, truck_spec)
    m = best.metrics(truck_spec)
    requested_qty = sum(ol["quantity"] for ol in order_lines)

    return {
        "fits": best.unfitted_count == 0,
        "strategy": best.strategy,
        "requested_count": requested_qty,
        "fitted_count": best.fitted_count,
        "unfitted_count": best.unfitted_count,
        "unfitted_detail": best.unfitted,
        "metrics": m,
        "truck": {
            "length_in": truck_spec["length_in"],
            "width_in": truck_spec["width_in"],
            "height_in": truck_spec["height_in"],
            "effective_height_in": truck_spec["height_in"] - DOOR_TRACK_LOSS_IN,
            "max_payload_lb": truck_spec["max_payload_lb"],
            "cargo_volume_cft": truck_spec.get("cargo_volume_cft"),
        },
        "placements": [
            {
                "seq": p.seq,
                "model_code": p.model_code,
                "x_in": round(p.x, 2), "y_in": round(p.y, 2), "z_in": round(p.z, 2),
                "dim_x_in": round(p.dim_x, 2),
                "dim_y_in": round(p.dim_y, 2),
                "dim_z_in": round(p.dim_z, 2),
                "weight_lb": round(p.weight_lb, 1),
                "lane": p.lane, "layer": p.layer,
            }
            for p in best.placements
        ],
    }


# Backwards compatibility shim: some tests still import these names
def pair_pack(order_lines, master, truck_spec, sort_strategy: str = "height_desc"):
    """Legacy entry — delegates to find_best (multi-strategy picks best)."""
    return find_best(order_lines, master, truck_spec)
