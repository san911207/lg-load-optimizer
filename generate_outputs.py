"""
샘플 입력으로 Excel 리포트 + 3D HTML 생성
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from engine.packer import simulate_pack
from engine.viz import build_3d_figure
from engine.report import write_excel_report

XL = Path(__file__).parent / "data" / "sample_input.xlsx"
OUT = Path(__file__).parent / "outputs"
OUT.mkdir(exist_ok=True)

df_master = pd.read_excel(XL, sheet_name="Model_Master")
df_loads  = pd.read_excel(XL, sheet_name="Loads")
df_trucks = pd.read_excel(XL, sheet_name="Truck_Master")

master = df_master.set_index("model_code").to_dict("index")
truck_map = df_trucks.set_index("truck_type").to_dict("index")
for tt, spec in truck_map.items():
    spec["truck_type"] = tt

results = []
for load_id, grp in df_loads.groupby("load_id"):
    truck_type = grp["truck_type"].iloc[0]
    order_lines = grp[["model_code","quantity"]].to_dict("records")
    res = simulate_pack(
        load_id=load_id,
        order_lines=order_lines,
        model_master=master,
        truck_spec=truck_map[truck_type],
        bigger_first=True,
    )
    results.append(res)
    # 3D HTML 저장
    fig = build_3d_figure(res)
    html_path = OUT / f"{load_id}_3d.html"
    fig.write_html(html_path, include_plotlyjs="cdn")
    print(f"  ✅ {load_id}_3d.html 생성")

# Excel 리포트
xlsx_path = OUT / "load_report.xlsx"
write_excel_report(results, xlsx_path)
print(f"  ✅ load_report.xlsx 생성")
print(f"\n출력 디렉토리: {OUT}")
