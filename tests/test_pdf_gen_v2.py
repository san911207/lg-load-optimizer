"""Tests for the v2 1-page PDF work order generator."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.pdf_gen_v2 import generate_work_order_v2
from engine.router import solve


@pytest.fixture(scope="module")
def sample():
    base = "/Users/sangkyu/projects/load_optimizer/data/sample_input.xlsx"
    master_df = pd.read_excel(base, "Model_Master")
    truck_df = pd.read_excel(base, "Truck_Master")
    loads = pd.read_excel(base, "Loads")
    master = master_df.set_index("model_code").to_dict("index")
    master["LDFN4542S"].update({"stackable": True, "load_bear_lb": 132.3, "fragile": False})
    master["LWS3063ST"].update({"stackable": True, "load_bear_lb": 198.4, "fragile": False})
    trucks = truck_df.set_index("truck_type").to_dict("index")
    return master, trucks, loads


def test_pdf_v2_renders_for_real_load(sample):
    master, trucks, loads = sample
    order = loads[loads["load_id"] == "L001"][["model_code", "quantity"]].to_dict("records")
    r = solve(order, master, trucks["26ft"], time_budget_s=4.0)
    pdf = generate_work_order_v2(
        r, load_id="L001", truck_label="26 ft Penske",
        truck_spec=trucks["26ft"], master=master, driver="J. Martinez",
    )
    assert pdf.startswith(b"%PDF-1."), "Output is not a PDF file"
    assert len(pdf) > 4000, f"PDF suspiciously small ({len(pdf)} bytes)"
    # Phase B / Forklift-Op fix: PDF now uses row_h=14 pt so a 44-item load
    # legitimately spills to page 2 (was previously truncated with a
    # "Page 1 of 1" lie). Accept 1 or 2 pages — what matters is no truncation.
    page_count = pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ")
    assert page_count in (1, 2), (
        f"PDF should be 1 or 2 pages for a 44-item load, found {page_count}"
    )


def test_pdf_v2_handles_small_load(sample):
    """Tiny 3-item load — PDF must still render cleanly."""
    master, trucks, _ = sample
    order = [{"model_code": "LDFN4542S", "quantity": 3}]
    r = solve(order, master, trucks["26ft"], time_budget_s=5.0)
    pdf = generate_work_order_v2(r, "TEST", "26 ft", trucks["26ft"], master)
    assert pdf.startswith(b"%PDF-1.")


def test_pdf_v2_page_count_grows_with_large_loads(sample):
    """A 100-item load must spill past page 1 and emit Page 2.

    Catches regressions where the row-height shrinks below readable size
    (UX Director audit) or "1 of 1" footer lies about truncation.
    """
    master, trucks, _ = sample
    order = [{"model_code": "LDFN4542S", "quantity": 100}]
    r = solve(order, master, trucks["53ft"], time_budget_s=5.0)
    pdf = generate_work_order_v2(
        r, load_id="LARGE", truck_label="53 ft Wabash",
        truck_spec=trucks["53ft"], master=master,
    )
    page_count = pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ")
    assert page_count >= 2, f"100-item load should span >=2 pages, found {page_count}"
    # Footer must reflect the real page count, not "1 of 1".
    assert b"Page 1 of 1" not in pdf


def test_pdf_v2_position_label_uses_floor_not_z0(sample):
    """The sequence table must render 'Floor' or 'Layer N' / 'On top of #N',
    never the internal coordinate 'z=0' (Tech Writer + Forklift Op fix).
    """
    master, trucks, _ = sample
    order = [{"model_code": "LDFN4542S", "quantity": 3}]
    r = solve(order, master, trucks["26ft"], time_budget_s=4.0)
    pdf = generate_work_order_v2(
        r, load_id="POS-LBL", truck_label="26 ft",
        truck_spec=trucks["26ft"], master=master,
    )
    # We can't inspect rendered text inside the PDF stream easily, but we
    # can verify the layer-label helper directly.
    from engine.pdf_gen_v2 import _layer_label
    floor_p = {"seq": 1, "x_in": 0, "y_in": 0, "z_in": 0,
               "dim_x_in": 30, "dim_y_in": 30, "dim_z_in": 30}
    stacked_p = {"seq": 2, "x_in": 0, "y_in": 0, "z_in": 30,
                 "dim_x_in": 30, "dim_y_in": 30, "dim_z_in": 30}
    assert _layer_label(floor_p, [floor_p, stacked_p]) == "Floor"
    on_top = _layer_label(stacked_p, [floor_p, stacked_p])
    assert "On top of" in on_top or "Layer" in on_top
    assert "z=" not in on_top


def test_pdf_v2_sku_ellipsis_for_long_codes():
    """SKU > 17 chars must be truncated with ellipsis (Tech Writer fix)."""
    from engine.pdf_gen_v2 import _sku_display
    assert _sku_display("ABC") == "ABC"  # short — unchanged
    assert _sku_display("OLED55C4PUA.AUSWLJR") == "OLED55C4PUA.AUSW…"
    assert len(_sku_display("X" * 50)) == 17
    assert _sku_display("X" * 50).endswith("…")


def test_pdf_v2_handles_empty_master(sample):
    """master=None must not crash — boxes fall back to neutral grey."""
    _, trucks, _ = sample
    minimal_master = {"X": {"category": "Unknown", "width_in": 10, "depth_in": 10,
                            "height_in": 10, "weight_lb": 5, "stackable": True, "fragile": False}}
    order = [{"model_code": "X", "quantity": 2}]
    r = solve(order, minimal_master, trucks["26ft"], time_budget_s=2.0)
    pdf = generate_work_order_v2(r, "EMPTY", "26 ft", trucks["26ft"], master=None)
    assert pdf.startswith(b"%PDF-1.")
