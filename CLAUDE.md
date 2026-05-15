# LG Appliance Truck Load Optimizer

> Internal SCM tool for LG Electronics US — simulates appliance loading into box trucks and dry vans, recommends the best-fit truck, and generates 3D load diagrams + worker sequence guides.

## What this is

Load planners receive shipment orders (e.g. "6 refrigerators + 8 washers + 8 dryers + 10 dishwashers + 4 wall ovens"). They need to answer **two questions fast**:

1. **Does it fit in a 26ft box truck, or do we need a 53ft dry van?** (Go/No-Go decision)
2. **How exactly do we load it?** (3D diagram + step-by-step worker guide)

This tool answers both. Real shipments today hit only ~800 CBF when theoretical capacity is 1,400 CBF — load utilization is being left on the table.

## Quick start

```bash
# Install
pip install -r requirements.txt

# Run Streamlit PoC
streamlit run app.py

# Or simulate in Python
python -c "from engine.best_packer import simulate; ..."

# Docker (for company cloud deployment)
docker-compose up
```

## Project structure

```
load_optimizer/
├── CLAUDE.md                 ← you are here
├── README.md                 ← user-facing intro
├── app.py                    ← Streamlit UI (Phase 0 PoC)
├── engine/
│   ├── best_packer.py        ← pair-packing algorithm (CORE — use this)
│   ├── packer.py             ← py3dbp wrapper (legacy, kept for comparison)
│   ├── viz.py                ← Plotly 3D visualization
│   ├── report.py             ← Excel report generator
│   ├── pdf_gen.py            ← PDF work order (reportlab)
│   ├── email_sender.py       ← Email module (SMTP, HTML, attachments)
│   └── email_ui.py           ← Streamlit email panel
├── data/
│   ├── sample_input.xlsx     ← 28 LG models + 4 load scenarios
│   └── build_sample_excel.py
├── docs/
│   ├── ALGORITHM.md          ← pair-packing logic spec
│   ├── DESIGN_SPEC.md        ← UI screens (Step 1 + Step 2A + Step 2B)
│   ├── EMAIL_SETUP.md        ← SMTP setup (M365 / Gmail / SES / relay)
│   └── DEPLOYMENT.md         ← Docker / cloud deployment
├── tests/
│   ├── test_packer.py        ← 13 algorithm tests
│   └── test_email.py         ← 24 email tests (mock SMTP)
├── outputs/                  ← generated 3D HTML + Excel reports
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Core algorithm: pair-packing

**Standard 3D bin-packing (py3dbp) leaves space on the table** — it uses First-Fit Decreasing and won't achieve max lane count. For a washer (745mm wide) in an 8ft truck (2,438mm), py3dbp uses only 1 lane when 3 are possible.

Our **pair-packing algorithm** (`engine/best_packer.py`):

1. **Group by dimensions** — washers and dryers share the same box (745×830×1050 mm), so pack them together
2. **Force max lanes** — `n_lanes = truck_width // box_width`
3. **2-tier stack** when allowed (stackable + load_bear ≥ weight + not fragile + 2×height ≤ effective truck height)
4. **Try 3 sort strategies** (height_desc, weight_desc, volume_desc), auto-select best (most fitted, then most compact)

### Result on sample load (36 units, 26ft truck)

| Strategy | Fitted | Length used | Compactness |
|----------|--------|-------------|-------------|
| py3dbp baseline | 36/36 | 25.8 ft | 99.4% |
| **Pair-packing 🏆** | **36/36** | **24.2 ft** | **93.0%** |

All models hit max lane count (3 across for washer/dryer/dishwasher, 2 for fridge/oven). 1.7 ft (22 in) buffer in rear allows room for last-minute additions.

See `docs/ALGORITHM.md` for full spec.

## Critical real-world constraints (validated via web research)

1. **All appliances must stay upright** — never lay on side
   - Washers: drum/suspension damage, oil leak (Move4U Movers)
   - Refrigerators (especially French-door): compressor damage (Best Buy GE customer service)
   - Source verified: Allied Movers, Angie's List, Affinity Moving (Jan-Apr 2026)

2. **Roll-up door track on 26ft box trucks** — rear 5 ft of ceiling loses ~10" (250mm)
   - Door rolls up into ceiling space
   - Effective height: 8.5 ft → 7.67 ft (only at rear)
   - Front ~21 ft of truck has full height
   - Source verified: U-Haul (8'3" interior, 6'10" door), Penske (8'7"), theupfitinsider.com

3. **Heavy-first, front-loaded** — refrigerators at cab end
   - Improves vehicle stability (60/40 weight rule)
   - Source: Olympia Moving, Advanced Moving, Freightwaves

4. **Stack load limits** — load_bear must be ≥ weight of item above
   - Washer (95kg) under-stack OK for dryer (75kg) on top
   - Refrigerator (155kg) cannot be stacked (height alone forbids)

5. **LIFO unloading** — dishwashers/ovens loaded last, unloaded first

## UI screens (final design)

Three screens. See `docs/DESIGN_SPEC.md` for SVG mockups & exact spec.

### Step 1 — Decision: "Can it fit?"
- Side-by-side truck cards: 26ft Box (recommended) vs 53ft Van (oversized)
- 4 KPI cards per truck: Units · Length used · **Load volume** · Weight
- **3-bar Load rate chart**: Length / Volume / Weight utilization
- Comparison table: 26ft vs 53ft on all 3 utilization metrics
- All constraints checklist (6 items, all must pass)
- All units displayed in **US units** (ft, in, lb, ft³)

### Step 2-A — 3D load diagram (manager view)
- Isometric cabinet projection (30°)
- All 36 boxes rendered with 3 visible faces (front/top/right)
- Zone outlines (A=Refrigerator, B=Washer+Dryer, C=Dishwasher, D=Oven)
- Door track shown only in rear corner (not full ceiling)
- Zone breakdown table: rows × lanes × tiers per zone

### Step 2-B — Sequence diagram (worker view)
- Pre-load checklist (tools, dock lineup order)
- 5-step side-view mini diagrams showing truck filling progressively
- Each step: position (ft), qty, R×L×T layout, handling tip
- Secure & inspect final checklist
- PDF print button (worker takes to dock)

## Data model

### Excel input (3 sheets per file)

**Sheet 1: `Model_Master`**
| Column | Type | Notes |
|--------|------|-------|
| model_code | str | PK, e.g. "LF29H8330S" |
| category | str | refrigerator/washer/dryer/dishwasher/oven |
| width_mm, depth_mm, height_mm | int | box dimensions |
| weight_kg | float | item weight |
| this_side_up | bool | always True for appliances |
| stackable | bool | can stack same model on top? |
| load_bear_kg | float | max weight on top |
| fragile | bool | restrict stacking other items on top |

**Sheet 2: `Loads`** (multiple loads per file)
| Column | Type |
|--------|------|
| load_id | str (e.g. "L001") |
| model_code | str |
| quantity | int |

**Sheet 3: `Truck_Master`**
| Column | Type | Notes |
|--------|------|-------|
| truck_type | str | "26ft" or "53ft" |
| length_mm, width_mm, height_mm | int | interior dims |
| max_payload_kg | float | payload capacity |
| door_type | str | "roll_up" or "swing" |
| door_track_loss_mm | int | 250 for roll-up, 0 for swing |

### Calibrations applied to sample master

- `LDFN4542S` (dishwasher): set `stackable=True`, `load_bear_kg=60`, `fragile=False` — sturdy box, lightweight item
- `LWS3063ST` (wall oven): set `stackable=True`, `load_bear_kg=90`, `fragile=False` — sturdy shipping box (the product is fragile but the box can take a same-model stack)

These calibrations matter — without them, the algorithm fails to fit all 36 units. Document this when adding new SKUs.

See `data/build_sample_excel.py` for the sample data builder.

## Phase roadmap

### Phase 0 — Streamlit PoC ✅ DONE
- Single-file Streamlit app
- Pair-packing engine (`engine/best_packer.py`)
- Excel report + Plotly 3D HTML output
- Sample data with 28 LG models

### Phase 1 — Production internal tool (NEXT)
- Split: FastAPI backend + React frontend
- PostgreSQL for model master (SQLite in PoC)
- Real 3D viewer in React (Three.js / react-three-fiber)
- PDF work order generator (reportlab)
- Docker Compose for company cloud deployment
- SSO/SAML integration
- Multi-user, audit log

### Phase 2 — Enterprise integration
- LG ERP/SAP integration (model master sync, order import)
- TMS integration (load assignment feedback loop)
- Multi-stop delivery (LIFO across drops)
- Real-time dock scheduling

## Key TODOs for Claude Code (Phase 1)

When working on this, prioritize in order:

1. **Set up backend/frontend split**
   - `backend/` → FastAPI (port 8000), serves `/api/*`
   - `frontend/` → Vite + React + TypeScript + Tailwind (port 5173)
   - Keep current Streamlit `app.py` as reference, move to `streamlit_poc/`

2. **Implement real 3D viewer** in React
   - Use `@react-three/fiber` + `@react-three/drei`
   - Camera: isometric 30° default + free-orbit
   - Render boxes from `placements[]` array (returned by `simulate()`)
   - Color-code by category, label by model_code
   - Door track as semi-transparent red box in rear corner

3. **PDF work order generator** (`engine/pdf_gen.py`)
   - Library: `reportlab` (already in requirements)
   - One-page A4/Letter print-friendly layout
   - Title + truck info + 5-step side-view mini diagrams + pre-load checklist + secure checklist
   - Worker takes printout to the dock

4. **Test coverage** for `engine/best_packer.py`
   - Edge cases: oversized item, single item, all-fragile group, mixed dims
   - Validate against py3dbp baseline (`engine/packer.py`)
   - Property test: simulate result should have no overlapping boxes

5. **Deploy to company cloud**
   - Multi-stage Dockerfile (backend + frontend in one image)
   - docker-compose.yml: app + postgres + nginx
   - See `docs/DEPLOYMENT.md`
   - SSO via SAML or OAuth (depends on LG IT)

## Working style notes (for Claude Code)

- **Korean-friendly code comments OK** — Sangkyu reads both Korean and English fluently. Lean toward English for code, Korean for inline TODOs is fine
- **US units in UI**, mm/kg internally (convert at display layer only)
- **No mock data in production** — always read from Excel or DB
- **Validate every constraint** — door track loss, upright rule, stack limits, fragile flag
- **Test with sample_input.xlsx** before committing — should always produce 36/36 fitted, 24.2 ft used
- **Don't rewrite the algorithm** — `engine/best_packer.py` is validated. Add features around it instead.

## Email notifications

The tool can send work order emails to dock managers / drivers / planners with simulation summary + attachments.

### Quick test (without sending)

```bash
export SMTP_HOST=smtp.office365.com
export SMTP_FROM_ADDRESS=load-optimizer@lg.com

python -c "
from engine.best_packer import simulate
from engine.email_sender import SMTPConfig, send_load_report
import pandas as pd

xl = 'data/sample_input.xlsx'
master = pd.read_excel(xl, 'Model_Master').set_index('model_code').to_dict('index')
master['LDFN4542S'].update({'stackable': True, 'load_bear_kg': 60, 'fragile': False})
master['LWS3063ST'].update({'stackable': True, 'load_bear_kg': 90, 'fragile': False})
truck = pd.read_excel(xl, 'Truck_Master').set_index('truck_type').to_dict('index')['26ft']
orders = [
    {'model_code': 'LF29H8330S', 'quantity': 6},
    {'model_code': 'WM4000HWA',  'quantity': 8},
    {'model_code': 'DLEX4000W',  'quantity': 8},
    {'model_code': 'LDFN4542S',  'quantity': 10},
    {'model_code': 'LWS3063ST',  'quantity': 4},
]
result = simulate(orders, master, truck)

config = SMTPConfig.from_env()
info = send_load_report(
    config=config,
    to=['dock@lg.com'],
    load_id='L001',
    simulation_result=result,
    dry_run=True,   # preview only
)
print(info)
"
```

### CLI usage

```bash
python -m engine.email_sender \
    --load L001 \
    --result outputs/L001_result.json \
    --to dock@lg.com \
    --cc planner@lg.com \
    --attach outputs/L001.pdf outputs/load_report.xlsx \
    --dry-run     # remove to actually send
```

### Files

- `engine/email_sender.py` — core module (SMTP, HTML rendering, validation)
- `engine/email_ui.py` — Streamlit UI panel
- `tests/test_email.py` — 24 mock tests (all passing)
- `docs/EMAIL_SETUP.md` — full setup guide for M365 / Gmail / SES / internal relay

### Provider setup

See `docs/EMAIL_SETUP.md`. Most likely at LG: **Microsoft 365 SMTP + App Password**.

## Common commands

```bash
# Run sample simulation
python -c "
from engine.best_packer import simulate
import pandas as pd
xl = 'data/sample_input.xlsx'
master = pd.read_excel(xl, 'Model_Master').set_index('model_code').to_dict('index')
truck = pd.read_excel(xl, 'Truck_Master').set_index('truck_type').to_dict('index')['26ft']
orders = [
  {'model_code': 'LF29H8330S', 'quantity': 6},
  {'model_code': 'WM4000HWA',  'quantity': 8},
  {'model_code': 'DLEX4000W',  'quantity': 8},
  {'model_code': 'LDFN4542S',  'quantity': 10},
  {'model_code': 'LWS3063ST',  'quantity': 4},
]
result = simulate(orders, master, truck)
print(f'Fits: {result[\"fits\"]}, {result[\"fitted_count\"]}/{result[\"requested_count\"]}')
print(f'Length used: {result[\"metrics\"][\"x_used_ft\"]} ft ({result[\"metrics\"][\"compactness_pct\"]}%)')
"

# Generate all sample outputs (4 loads → 4 HTML + 1 Excel)
python generate_outputs.py

# Start Streamlit dev server
streamlit run app.py

# Run tests
pytest tests/

# Docker (local)
docker-compose up
```

## Contact

Author: Sangkyu (LG Electronics US, SCM)
This is an internal tool. Not for external distribution.
