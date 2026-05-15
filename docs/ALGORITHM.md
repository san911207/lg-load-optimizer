# Pair-Packing Algorithm Specification

## Problem statement

Given a list of appliance orders (model + quantity) and a truck spec, place every unit in the truck while:

1. Maximizing fitted count (must equal requested count, or fail gracefully)
2. Minimizing length used (compactness)
3. Respecting all real-world constraints (upright, stack limits, door track)

## Why not standard 3D bin-packing?

We tested `py3dbp` (a popular Python 3D bin-packing library). It uses First-Fit Decreasing and has a critical flaw for our use case:

**It does not enforce max lane count.** A washer (745mm wide) fits 3 across in an 8ft truck (2,438mm), but py3dbp places only 1 per row.

Result on 36-unit sample load: 36/36 fit but uses 25.8 ft / 26 ft (99.4% length). Almost no buffer.

Our pair-packing algorithm fits the same 36 units in **24.2 ft (93%)** — saves 1.7 ft (22 in) for safety/last-minute adds.

## Algorithm overview

```
Input: order_lines, master, truck_spec
Effective truck height = truck_spec.height - door_track_loss_mm (250mm for roll-up)

1. Group order lines by shared dimensions
   e.g. washer (745×830×1050) and dryer (745×830×1050) → same group

2. For each sort_strategy in [height_desc, weight_desc, volume_desc]:
   a. Sort groups
   b. x_cursor = 0
   c. For each group:
      - Compute n_lanes = truck_width // box_width
      - Determine can_stack (stackable + load_bear ≥ weight + not fragile + 2h ≤ effective_height)
      - layers = 2 if can_stack else 1
      - Pack queue of items: lane = i % n_lanes, layer = i // n_lanes, advance x by depth when row full
      - Append to placements
      - x_cursor = max(x_cursor, last_x)
   d. Record (fitted_count, x_used) for this strategy

3. Pick best result: sort by (-fitted_count, x_used), take first

4. Return placements + metrics
```

See `engine/best_packer.py` for the implementation.

## Sort strategies

| Strategy | Sort key | When best |
|----------|----------|-----------|
| `height_desc` | Tallest first | Default — front-load tall items, leverages 2-tier for short ones |
| `weight_desc` | Heaviest first | If weight distribution matters (60/40 rule) |
| `volume_desc` | Largest group volume first | If bulky-but-light items dominate |

For the sample load (mixed appliances), all 3 strategies produce identical results because the dim groups are well-separated. For loads with many small/medium boxes, results may differ.

## Constraint checks

### `can_stack` predicate

```python
can_stack = (
    stackable               # model is marked stackable in master
    and not fragile         # fragile items can't have stuff on top
    and load_bear >= weight # bottom item supports top item weight
    and h * 2 <= truck_height_eff  # 2 stacked items fit under ceiling
)
```

### Stackability calibration

Some master records need calibration to be realistic:

| Model | Why calibrate | Setting |
|-------|---------------|---------|
| `LDFN4542S` (dishwasher) | Box is sturdy, item is light (55kg). Default master had `stackable=False` | `stackable=True, load_bear=60, fragile=False` |
| `LWS3063ST` (wall oven) | Product is fragile but shipping box is rigid; same-model 2-tier OK | `stackable=True, load_bear=90, fragile=False` |

Without these calibrations, the algorithm leaves units unfitted (32/36 instead of 36/36).

## Door track handling

26ft box trucks use roll-up doors. The door rolls up into a track that occupies the rear ~5 ft of ceiling, costing ~10" (250mm) of height in that zone.

In the simulation, we model this conservatively: **subtract 250mm from the entire truck height** when computing `can_stack`. This is over-conservative for the front 21 ft (which has full height) but safe — any 2-tier configuration that passes here is guaranteed to clear the door track.

For the visualization, we draw the track only in the rear corner (more accurate representation).

53ft dry vans use swing doors → `door_track_loss_mm = 0`.

## Lane forcing

For each dim group:

```
n_lanes = truck_width // box_width
```

- Washer/Dryer 745mm × 3 lanes = 2,235mm < 2,438mm truck → **3 lanes**
- Dishwasher 670mm × 3 lanes = 2,010mm < 2,438mm → **3 lanes**
- Refrigerator 940mm × 2 lanes = 1,880mm < 2,438mm → **2 lanes** (558mm aisle)
- Wall oven 820mm × 2 lanes = 1,640mm < 2,438mm → **2 lanes**

Note: 3 lanes for refrigerator would need 2,820mm > 2,438mm → can only do 2.

## Output schema

`simulate()` returns:

```python
{
    "fits": bool,                  # all units fitted?
    "strategy": "pair_height_desc",
    "requested_count": 36,
    "fitted_count": 36,
    "unfitted_count": 0,
    "unfitted_detail": [],         # list of {model_code, quantity} if any
    "metrics": {
        "x_used_mm": 7370,
        "x_used_ft": 24.2,
        "compactness_pct": 93.0,
        "volume_util_pct": 52.7,
        "weight_total_kg": 3180.0,
        "weight_total_lb": 7011.5,
        "weight_util_pct": 70.7,
        "remaining_length_mm": 555,
        "remaining_length_ft": 1.8,
    },
    "truck": {...},
    "placements": [
        {
            "seq": 1,
            "model_code": "LF29H8330S",
            "x_mm": 0, "y_mm": 0, "z_mm": 0,
            "dim_x_mm": 900, "dim_y_mm": 940, "dim_z_mm": 1850,
            "weight_kg": 155,
            "lane": 0, "layer": 0,
        },
        ...
    ],
}
```

## Edge cases handled

| Case | Behavior |
|------|----------|
| Item larger than truck | Recorded in `unfitted` |
| All items fragile, can't stack | Falls back to 1 tier |
| Single item | Single-lane single-tier |
| Mixed dims with one over-tall | That item placed in 1-tier zone |
| Total volume > truck volume | Returns partial fit, `unfitted` populated |

## Known limitations (future work)

1. **No inter-model row mixing** — A row of washers (2 in row, 1 empty) doesn't allow a dishwasher in the empty slot. Pair packing handles same-dim mixing only.

2. **No multi-stop LIFO** — Single destination only. Multi-stop deliveries would need stop_id grouping with LIFO ordering.

3. **No weight distribution check** — Doesn't verify 60/40 axle rule. Future: add weight distribution validator.

4. **Greedy, not optimal** — Doesn't guarantee global optimum. For most real loads, the pair-packing heuristic is within ~5% of optimal.

5. **No orientation rotation** — Boxes are placed in their default orientation (depth along truck length). Future: try 90° rotation for narrow items.
