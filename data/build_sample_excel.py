"""
=========================================================================
샘플 Excel 빌더 - 실제 LG 제품 데이터 기반
=========================================================================
출처: LG USA 공식 사이트, Best Buy Q&A, Home Depot
모든 치수는 PACKAGING(포장박스) 기준, 무게는 총 무게(포장재 포함)

단위: mm, kg (SCM 표준)
변환: 1 inch = 25.4 mm, 1 lb = 0.453592 kg
포장 패딩 가산: 제품 dim + 50~100mm (실측 기반 일반화)
=========================================================================
"""
import pandas as pd
from pathlib import Path

OUT_PATH = Path(__file__).parent / "sample_input.xlsx"

# ────────────────────────────────────────────────────────────────
# Sheet 1: Model_Master  (모델 마스터)
# ────────────────────────────────────────────────────────────────
# Width × Depth × Height 는 포장박스 외형(mm)
# stackable: 위에 다른 박스를 올릴 수 있는가
# load_bear_kg: 그 박스 위에 올릴 수 있는 최대 무게
# this_side_up: 상하 회전 금지 (가전 99%는 True)
# ────────────────────────────────────────────────────────────────

model_master = [
    # ───── 냉장고 (Refrigerators) ─────
    # 출처: LG USA + 표준 포장 패딩 추가
    {"model_code":"LF29H8330S","category":"Refrigerator_FrenchDoor4Door","width_mm":940,"depth_mm":900,"height_mm":1850,"weight_kg":155,"this_side_up":True,"stackable":False,"load_bear_kg":0,"fragile":True,"notes":"29 cu.ft 4-door French Door"},
    {"model_code":"LF25S6560S","category":"Refrigerator_FrenchDoor","width_mm":860,"depth_mm":820,"height_mm":1810,"weight_kg":135,"this_side_up":True,"stackable":False,"load_bear_kg":0,"fragile":True,"notes":"25 cu.ft 33in Standard-Depth"},
    {"model_code":"LRFLC2706S","category":"Refrigerator_CounterDepth","width_mm":920,"depth_mm":770,"height_mm":1840,"weight_kg":140,"this_side_up":True,"stackable":False,"load_bear_kg":0,"fragile":True,"notes":"27 cu.ft Counter-Depth MAX"},
    {"model_code":"LRFLS3206S","category":"Refrigerator_FrenchDoor","width_mm":960,"depth_mm":880,"height_mm":1870,"weight_kg":160,"this_side_up":True,"stackable":False,"load_bear_kg":0,"fragile":True,"notes":"32 cu.ft Standard-Depth MAX"},
    {"model_code":"LF24Z6530S","category":"Refrigerator_FrenchDoor","width_mm":850,"depth_mm":780,"height_mm":1800,"weight_kg":125,"this_side_up":True,"stackable":False,"load_bear_kg":0,"fragile":True,"notes":"24 cu.ft Slim French Door"},
    {"model_code":"LRSXS2706V","category":"Refrigerator_SideBySide","width_mm":910,"depth_mm":820,"height_mm":1810,"weight_kg":130,"this_side_up":True,"stackable":False,"load_bear_kg":0,"fragile":True,"notes":"27 cu.ft Side-by-Side"},

    # ───── 세탁기/건조기 (Laundry) ─────
    # WM4000HWA: 27"W × 39"H × 30.25"D 제품 → 박스 +60mm 패딩
    {"model_code":"WM4000HWA","category":"Washer_FrontLoad","width_mm":745,"depth_mm":830,"height_mm":1050,"weight_kg":95,"this_side_up":True,"stackable":True,"load_bear_kg":95,"fragile":False,"notes":"4.5 cu.ft Front Load Washer"},
    {"model_code":"DLEX4000W","category":"Dryer_Electric","width_mm":745,"depth_mm":830,"height_mm":1050,"weight_kg":75,"this_side_up":True,"stackable":True,"load_bear_kg":95,"fragile":False,"notes":"7.4 cu.ft Electric Dryer"},
    {"model_code":"DLGX4001W","category":"Dryer_Gas","width_mm":745,"depth_mm":830,"height_mm":1050,"weight_kg":80,"this_side_up":True,"stackable":True,"load_bear_kg":95,"fragile":False,"notes":"7.4 cu.ft Gas Dryer"},
    {"model_code":"WT7900HBA","category":"Washer_TopLoad","width_mm":720,"depth_mm":770,"height_mm":1170,"weight_kg":70,"this_side_up":True,"stackable":False,"load_bear_kg":0,"fragile":False,"notes":"5.5 cu.ft Top Load Washer"},

    # ───── 디시워셔 / 오븐 / 레인지 ─────
    {"model_code":"LDFN4542S","category":"Dishwasher","width_mm":670,"depth_mm":730,"height_mm":920,"weight_kg":55,"this_side_up":True,"stackable":False,"load_bear_kg":0,"fragile":False,"notes":"24in Front Control Dishwasher"},
    {"model_code":"LRGL5825F","category":"Range_Gas","width_mm":830,"depth_mm":790,"height_mm":1230,"weight_kg":100,"this_side_up":True,"stackable":False,"load_bear_kg":0,"fragile":True,"notes":"5.8 cu.ft Gas Slide-in Range"},
    {"model_code":"LREL6325F","category":"Range_Electric","width_mm":830,"depth_mm":790,"height_mm":1230,"weight_kg":90,"this_side_up":True,"stackable":False,"load_bear_kg":0,"fragile":True,"notes":"6.3 cu.ft Electric Slide-in Range"},
    {"model_code":"LWS3063ST","category":"WallOven","width_mm":820,"depth_mm":720,"height_mm":890,"weight_kg":85,"this_side_up":True,"stackable":False,"load_bear_kg":0,"fragile":True,"notes":"30in Single Wall Oven"},
    {"model_code":"LMV1764ST","category":"Microwave_OTR","width_mm":820,"depth_mm":520,"height_mm":500,"weight_kg":28,"this_side_up":True,"stackable":True,"load_bear_kg":30,"fragile":False,"notes":"1.7 cu.ft Over-the-Range MW"},
    {"model_code":"LMC0975ST","category":"Microwave_CounterTop","width_mm":620,"depth_mm":510,"height_mm":410,"weight_kg":18,"this_side_up":True,"stackable":True,"load_bear_kg":20,"fragile":False,"notes":"0.9 cu.ft Counter Top MW"},

    # ───── TV (OLED / QNED / NanoCell) ─────
    # OLED65C4: 65.4"W × 38.2"H × 7.9"D box
    {"model_code":"OLED65C4PUA","category":"TV_OLED_65","width_mm":1665,"depth_mm":205,"height_mm":975,"weight_kg":24,"this_side_up":True,"stackable":True,"load_bear_kg":15,"fragile":True,"notes":"65in OLED evo C4 (box: 65.4x38.2x7.9in)"},
    {"model_code":"OLED77C4PUA","category":"TV_OLED_77","width_mm":1900,"depth_mm":285,"height_mm":1135,"weight_kg":50,"this_side_up":True,"stackable":True,"load_bear_kg":15,"fragile":True,"notes":"77in OLED evo C4 (box: 74.6x44.5x11.2in)"},
    {"model_code":"OLED83C4PUA","category":"TV_OLED_83","width_mm":2040,"depth_mm":305,"height_mm":1230,"weight_kg":62,"this_side_up":True,"stackable":False,"load_bear_kg":0,"fragile":True,"notes":"83in OLED evo C4"},
    {"model_code":"OLED55C4PUA","category":"TV_OLED_55","width_mm":1430,"depth_mm":195,"height_mm":855,"weight_kg":18,"this_side_up":True,"stackable":True,"load_bear_kg":15,"fragile":True,"notes":"55in OLED evo C4"},
    {"model_code":"OLED65G4WUA","category":"TV_OLED_Gallery_65","width_mm":1675,"depth_mm":225,"height_mm":985,"weight_kg":28,"this_side_up":True,"stackable":True,"load_bear_kg":15,"fragile":True,"notes":"65in OLED evo G4 Gallery"},
    {"model_code":"75QNED85TUA","category":"TV_QNED_75","width_mm":1820,"depth_mm":255,"height_mm":1110,"weight_kg":42,"this_side_up":True,"stackable":True,"load_bear_kg":15,"fragile":True,"notes":"75in QNED 4K"},
    {"model_code":"50UQ7570PUJ","category":"TV_UHD_50","width_mm":1240,"depth_mm":175,"height_mm":790,"weight_kg":14,"this_side_up":True,"stackable":True,"load_bear_kg":15,"fragile":True,"notes":"50in UHD 4K"},
    {"model_code":"43UR7300PUE","category":"TV_UHD_43","width_mm":1100,"depth_mm":165,"height_mm":705,"weight_kg":11,"this_side_up":True,"stackable":True,"load_bear_kg":15,"fragile":True,"notes":"43in UHD 4K"},

    # ───── 모니터 (UltraGear / UltraFine) ─────
    {"model_code":"27GR93U-B","category":"Monitor_27","width_mm":770,"depth_mm":200,"height_mm":495,"weight_kg":9,"this_side_up":True,"stackable":True,"load_bear_kg":12,"fragile":True,"notes":"27in UltraGear 4K Gaming"},
    {"model_code":"32GP850-B","category":"Monitor_32","width_mm":860,"depth_mm":220,"height_mm":560,"weight_kg":11,"this_side_up":True,"stackable":True,"load_bear_kg":12,"fragile":True,"notes":"32in UltraGear QHD Nano IPS"},
    {"model_code":"32GX850A-B","category":"Monitor_32_OLED","width_mm":880,"depth_mm":230,"height_mm":580,"weight_kg":13,"this_side_up":True,"stackable":True,"load_bear_kg":12,"fragile":True,"notes":"32in UltraGear UHD OLED"},
    {"model_code":"27G810A-B","category":"Monitor_27","width_mm":770,"depth_mm":200,"height_mm":495,"weight_kg":9,"this_side_up":True,"stackable":True,"load_bear_kg":12,"fragile":True,"notes":"27in UltraGear 4K Dual Mode"},
]

df_master = pd.DataFrame(model_master)
# 부피(CBM) 자동 계산 = w*d*h / 1e9 (mm³→m³)
df_master["volume_cbm"] = (df_master["width_mm"] * df_master["depth_mm"] * df_master["height_mm"]) / 1_000_000_000
df_master["volume_cbm"] = df_master["volume_cbm"].round(4)

# ────────────────────────────────────────────────────────────────
# Sheet 2: Loads  (출하 로드)
# Load_ID로 그룹핑되어 각각 시뮬레이션됨
# ────────────────────────────────────────────────────────────────
loads = [
    # L001: 가전 위주 (NJ-DC1 행)
    {"load_id":"L001","model_code":"LF29H8330S","quantity":6, "destination":"NJ-DC1","pickup_date":"2026-05-15","truck_type":"26ft"},
    {"load_id":"L001","model_code":"WM4000HWA", "quantity":8, "destination":"NJ-DC1","pickup_date":"2026-05-15","truck_type":"26ft"},
    {"load_id":"L001","model_code":"DLEX4000W", "quantity":8, "destination":"NJ-DC1","pickup_date":"2026-05-15","truck_type":"26ft"},
    {"load_id":"L001","model_code":"LDFN4542S", "quantity":10,"destination":"NJ-DC1","pickup_date":"2026-05-15","truck_type":"26ft"},
    {"load_id":"L001","model_code":"LMV1764ST", "quantity":12,"destination":"NJ-DC1","pickup_date":"2026-05-15","truck_type":"26ft"},

    # L002: 대형 TV 위주 (CA-DC3 행, 53ft 트레일러)
    {"load_id":"L002","model_code":"OLED65C4PUA","quantity":40,"destination":"CA-DC3","pickup_date":"2026-05-16","truck_type":"53ft"},
    {"load_id":"L002","model_code":"OLED77C4PUA","quantity":20,"destination":"CA-DC3","pickup_date":"2026-05-16","truck_type":"53ft"},
    {"load_id":"L002","model_code":"OLED55C4PUA","quantity":30,"destination":"CA-DC3","pickup_date":"2026-05-16","truck_type":"53ft"},
    {"load_id":"L002","model_code":"75QNED85TUA","quantity":15,"destination":"CA-DC3","pickup_date":"2026-05-16","truck_type":"53ft"},

    # L003: 혼합 (TX-DC2 행, 53ft)
    {"load_id":"L003","model_code":"LF25S6560S","quantity":10,"destination":"TX-DC2","pickup_date":"2026-05-17","truck_type":"53ft"},
    {"load_id":"L003","model_code":"OLED65C4PUA","quantity":20,"destination":"TX-DC2","pickup_date":"2026-05-17","truck_type":"53ft"},
    {"load_id":"L003","model_code":"27GR93U-B", "quantity":50,"destination":"TX-DC2","pickup_date":"2026-05-17","truck_type":"53ft"},
    {"load_id":"L003","model_code":"32GP850-B", "quantity":40,"destination":"TX-DC2","pickup_date":"2026-05-17","truck_type":"53ft"},
    {"load_id":"L003","model_code":"LMC0975ST", "quantity":30,"destination":"TX-DC2","pickup_date":"2026-05-17","truck_type":"53ft"},

    # L004: 소규모 26ft (FL-DC4 행)
    {"load_id":"L004","model_code":"OLED55C4PUA","quantity":15,"destination":"FL-DC4","pickup_date":"2026-05-18","truck_type":"26ft"},
    {"load_id":"L004","model_code":"OLED65C4PUA","quantity":12,"destination":"FL-DC4","pickup_date":"2026-05-18","truck_type":"26ft"},
    {"load_id":"L004","model_code":"32GX850A-B","quantity":20,"destination":"FL-DC4","pickup_date":"2026-05-18","truck_type":"26ft"},
]

df_loads = pd.DataFrame(loads)

# ────────────────────────────────────────────────────────────────
# Sheet 3: Truck_Master  (트럭 마스터, 내부 적재공간 기준)
# ────────────────────────────────────────────────────────────────
trucks = [
    {"truck_type":"26ft","display_name":"26ft Box Truck","length_mm":7925,"width_mm":2438,"height_mm":2590,"max_payload_kg":4500},
    {"truck_type":"53ft","display_name":"53ft Dry Van Trailer","length_mm":16154,"width_mm":2591,"height_mm":2700,"max_payload_kg":20000},
]
df_trucks = pd.DataFrame(trucks)
# 적재 부피 자동 계산
df_trucks["cargo_volume_cbm"] = (df_trucks["length_mm"]*df_trucks["width_mm"]*df_trucks["height_mm"]) / 1_000_000_000
df_trucks["cargo_volume_cbm"] = df_trucks["cargo_volume_cbm"].round(2)

# ────────────────────────────────────────────────────────────────
# Excel 출력
# ────────────────────────────────────────────────────────────────
with pd.ExcelWriter(OUT_PATH, engine="openpyxl") as writer:
    df_master.to_excel(writer, sheet_name="Model_Master", index=False)
    df_loads.to_excel(writer, sheet_name="Loads", index=False)
    df_trucks.to_excel(writer, sheet_name="Truck_Master", index=False)

print(f"✅ Excel created: {OUT_PATH}")
print(f"   - Model_Master  : {len(df_master)} models")
print(f"   - Loads         : {len(df_loads)} order lines, {df_loads['load_id'].nunique()} Load IDs")
print(f"   - Truck_Master  : {len(df_trucks)} truck types")
print(f"\n[Load 요약]")
for lid, grp in df_loads.groupby("load_id"):
    total_qty = grp["quantity"].sum()
    truck = grp["truck_type"].iloc[0]
    dest = grp["destination"].iloc[0]
    print(f"  {lid} → {dest} ({truck}): {len(grp)} 라인 / {total_qty} EA")
