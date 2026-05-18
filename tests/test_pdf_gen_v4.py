"""Smoke tests for PDF v4 generator (the final CEO-approved layout).

These tests catch the IndexError that hit build #22 in production on a
real LG ERP master upload — ``aggregate_zones`` previously keyed by
(broad_category, footprint) and produced more zones than the
``"ABCDEFGHIJKL"`` letter pool could index. The new logic groups by
broad_category only, and zone-id letters auto-extend past Z.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine.pdf_gen_v4 import generate_work_order_v4
from engine.router import solve
from engine.zone_aggregator import _zone_letter, aggregate_zones


@pytest.fixture(scope="module")
def sample():
    base = "/Users/sangkyu/projects/load_optimizer/data/sample_input.xlsx"
    master_df = pd.read_excel(base, "Model_Master")
    truck_df = pd.read_excel(base, "Truck_Master")
    loads = pd.read_excel(base, "Loads")
    master = master_df.set_index("model_code").to_dict("index")
    master["DISH-001"].update({"stackable": True, "load_bear_lb": 132.3, "fragile": False})
    master["WOVEN-001"].update({"stackable": True, "load_bear_lb": 198.4, "fragile": False})
    trucks = truck_df.set_index("truck_type").to_dict("index")
    return master, trucks, loads


def test_pdf_v4_renders_for_l001(sample):
    """L001 (~44 items, many SKU variants) must produce a valid PDF.

    This is the regression for the build-#22 production IndexError —
    the user's real LG ERP master produced 30+ unique (cat, footprint)
    keys, exceeding the old 12-char letter pool.
    """
    master, trucks, loads = sample
    order = loads[loads["load_id"] == "L001"][["model_code", "quantity"]].to_dict("records")
    r = solve(order, master, trucks["26ft"], time_budget_s=4.0)
    pdf = generate_work_order_v4(
        r, load_id="L001", truck_label="26 ft Penske",
        truck_spec=trucks["26ft"], master=master, driver="J. Martinez",
    )
    assert pdf.startswith(b"%PDF-1.")
    assert len(pdf) > 4000


def test_pdf_v4_handles_many_categories_no_indexerror(sample):
    """Stress: a synthetic load with 30+ distinct SKU footprints across
    several categories must not crash the zone letter assignment."""
    _, trucks, _ = sample
    # Build a synthetic master with 30 unique SKUs across categories.
    master: dict = {}
    order: list = []
    cats = ["Refrigerator", "Washer", "Dryer", "Dishwasher", "Microwave",
            "Wall Oven", "Range", "TV", "Monitor", "AV"]
    for i in range(30):
        sku = f"GEN-{i:03d}"
        master[sku] = {
            "category": cats[i % len(cats)],
            "width_in": 20 + (i % 7),
            "depth_in": 24 + (i % 5),
            "height_in": 30 + (i % 6),
            "weight_lb": 50 + i,
            "stackable": True, "fragile": False,
        }
        order.append({"model_code": sku, "quantity": 1})
    r = solve(order, master, trucks["53ft"], time_budget_s=5.0)
    pdf = generate_work_order_v4(
        r, load_id="STRESS", truck_label="53 ft",
        truck_spec=trucks["53ft"], master=master,
    )
    assert pdf.startswith(b"%PDF-1.")


def test_zone_letter_overflow_past_z():
    """_zone_letter must extend past Z without raising — used by
    aggregate_zones when the letter pool is exhausted."""
    assert _zone_letter(0) == "A"
    assert _zone_letter(25) == "Z"
    assert _zone_letter(26) == "AA"
    assert _zone_letter(27) == "AB"
    # 100+ zones is unrealistic but must not crash
    assert isinstance(_zone_letter(100), str)


def test_aggregate_zones_groups_by_broad_category_only(sample):
    """Two SKUs with same category but DIFFERENT footprints should land
    in the SAME zone (matches CEO target design — refrigerators of mixed
    sizes still appear as "A · Refrigerator")."""
    _, trucks, _ = sample
    master = {
        "FR1": {"category": "Refrigerator", "width_in": 30, "depth_in": 30,
                "height_in": 68, "weight_lb": 300, "stackable": False, "fragile": False},
        "FR2": {"category": "Refrigerator", "width_in": 36, "depth_in": 32,
                "height_in": 70, "weight_lb": 320, "stackable": False, "fragile": False},
    }
    order = [{"model_code": "FR1", "quantity": 2}, {"model_code": "FR2", "quantity": 2}]
    r = solve(order, master, trucks["26ft"], time_budget_s=3.0)
    zones = aggregate_zones(r["placements"], master, pair_count=0)
    fridge_zones = [z for z in zones if z.broad_category == "refrigerator"]
    assert len(fridge_zones) == 1
    assert fridge_zones[0].item_count == 4
