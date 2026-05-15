# LG Appliance Truck Load Optimizer

Internal SCM tool for LG Electronics US that answers two questions in seconds:

1. **Can this shipment fit in a 26ft box truck — or do we need a 53ft van?**
2. **How exactly should we load it?** (3D layout + step-by-step worker guide)

## Why this exists

Real shipments today hit only ~800 CBF when theoretical capacity is 1,400 CBF. Load planners juggle Excel sheets and visual estimates. This tool removes the guesswork — drop in a load, get an instant answer with full 3D and worker instructions.

## Features

- ✅ **Pair-packing algorithm** — automatically pairs same-dim models (washer + dryer) for max lane utilization
- ✅ **Auto-strategy selection** — tries 3 strategies, picks best (most fitted, most compact)
- ✅ **Real constraints applied** — upright-only, roll-up door track loss (rear 5 ft × 10"), stack load limits
- ✅ **Two views**: 3D isometric (manager) + 5-step side view (worker)
- ✅ **US units** in UI (ft, in, lb, ft³), metric internally
- ✅ **Three utilization metrics**: Length / Volume / Weight load rate
- 📄 **PDF work order** print for dock (Phase 1)
- 🚀 **Docker-ready** for company cloud deployment

## Quick start

```bash
# Local
git clone <repo>
cd load_optimizer
pip install -r requirements.txt
streamlit run app.py

# Docker
docker-compose up
# → http://localhost:8501
```

## Sample result

Input: 6 refrigerators + 8 washers + 8 dryers + 10 dishwashers + 4 wall ovens (36 units, 7,002 lb, 932 ft³)

Output:
- **26ft Box Truck: ✅ Fits** — 24.2 ft used, 22" buffer
- Load rate: Length 93% · Volume 53% · Weight 71%
- 53ft Van: oversized (Volume 23%, Weight 16%)
- Recommendation: 26ft

## Documentation

- `CLAUDE.md` — full context for Claude Code (algorithm, design, deployment)
- `docs/ALGORITHM.md` — pair-packing logic spec
- `docs/DESIGN_SPEC.md` — UI screens
- `docs/DEPLOYMENT.md` — Docker / cloud setup

## Author

Sangkyu, LG Electronics US (SCM)
