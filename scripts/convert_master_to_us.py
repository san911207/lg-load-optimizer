"""
One-shot converter: sample_input.xlsx (mm/kg/cbm) → US units (in/lb/cft).
Run once to migrate the data file to the new unit convention.
"""
from pathlib import Path
import pandas as pd

MM_TO_IN = 1 / 25.4
KG_TO_LB = 2.20462
CBM_TO_CFT = 35.3147

XLSX = Path(__file__).resolve().parent.parent / "data" / "sample_input.xlsx"


def convert_master(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["model_code"] = df["model_code"]
    out["category"] = df["category"]
    out["width_in"] = (df["width_mm"] * MM_TO_IN).round(2)
    out["depth_in"] = (df["depth_mm"] * MM_TO_IN).round(2)
    out["height_in"] = (df["height_mm"] * MM_TO_IN).round(2)
    out["weight_lb"] = (df["weight_kg"] * KG_TO_LB).round(1)
    out["this_side_up"] = df["this_side_up"]
    out["stackable"] = df["stackable"]
    out["load_bear_lb"] = (df["load_bear_kg"] * KG_TO_LB).round(1)
    out["fragile"] = df["fragile"]
    out["notes"] = df["notes"]
    out["volume_cft"] = (df["volume_cbm"] * CBM_TO_CFT).round(2)
    return out


def convert_trucks(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["truck_type"] = df["truck_type"]
    out["display_name"] = df["display_name"]
    out["length_in"] = (df["length_mm"] * MM_TO_IN).round(2)
    out["width_in"] = (df["width_mm"] * MM_TO_IN).round(2)
    out["height_in"] = (df["height_mm"] * MM_TO_IN).round(2)
    out["max_payload_lb"] = (df["max_payload_kg"] * KG_TO_LB).round(0).astype(int)
    out["cargo_volume_cft"] = (df["cargo_volume_cbm"] * CBM_TO_CFT).round(1)
    return out


def main():
    master = pd.read_excel(XLSX, sheet_name="Model_Master")
    trucks = pd.read_excel(XLSX, sheet_name="Truck_Master")
    loads = pd.read_excel(XLSX, sheet_name="Loads")

    master_new = convert_master(master)
    trucks_new = convert_trucks(trucks)

    with pd.ExcelWriter(XLSX, engine="openpyxl") as w:
        master_new.to_excel(w, sheet_name="Model_Master", index=False)
        trucks_new.to_excel(w, sheet_name="Truck_Master", index=False)
        loads.to_excel(w, sheet_name="Loads", index=False)

    print(f"Converted: {XLSX}")
    print("Trucks (US units):")
    print(trucks_new.to_string())


if __name__ == "__main__":
    main()
