# UI Design Specification

Three screens total. All units in UI use US system (ft, in, lb, ft³). Algorithm internally uses mm/kg.

## Screen 1 — Decision: "Can it fit?"

**Goal**: Load planner answers "26ft or 53ft?" in <10 seconds.

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│ [Step 1 of 2] Can it fit?                                   │
│ Load L001 · NJ-DC1 · 36 units · 7,002 lb · 932 ft³          │
├─────────────────────────────────────────────────────────────┤
│ Load 구성 (table)                                           │
│ Model      Qty  Dim (in)        Unit vol  Total vol  Weight │
│ Fridge ●   6    37 × 35.4 × 73   55.3 ft³  332 ft³  2,052 lb│
│ Washer ●   8    29.3 × 32.7 × 41 22.9 ft³  183 ft³  1,672 lb│
│ ...                                                          │
├─────────────────────────────────────────────────────────────┤
│ Truck simulation results                                    │
│ ┌─────────────────────────┐  ┌─────────────────────────┐    │
│ │ ⭐ Recommended           │  │ 53ft Dry Van            │    │
│ │ 26ft Box Truck    [Fits]│  │ 53 × 8.5 × 8.9 ft       │    │
│ │ 26 × 8 × 8.5 ft         │  │                         │    │
│ │ ┌───┐┌───┐┌───┐┌───┐    │  │ ┌───┐┌───┐┌───┐┌───┐    │    │
│ │ │36 ││24 ││932││7,002│   │  │ │36 ││24 ││932││7,002│   │    │
│ │ │/36││ft ││ft³││ lb │   │  │ │/36││ft ││ft³││ lb │   │    │
│ │ └───┘└───┘└───┘└───┘    │  │ └───┘└───┘└───┘└───┘    │    │
│ │ Load rate:              │  │ Load rate:              │    │
│ │ Length [████████░] 93%  │  │ Length [████░░░░░] 46% │    │
│ │ Volume [████░░░░░] 53%  │  │ Volume [██░░░░░░░] 23% │    │
│ │ Weight [███████░░] 71%  │  │ Weight [██░░░░░░░] 16% │    │
│ │ ✓ Right size            │  │ ⚠ Oversized · 28.8 ft  │    │
│ └─────────────────────────┘  └─────────────────────────┘    │
├─────────────────────────────────────────────────────────────┤
│ 왜 26ft가 추천? (comparison table)                          │
│              26ft Box   53ft Van   차이                     │
│ Length 장입률  93%        46%       +47%p                    │
│ Volume 장입률  53%        23%       +30%p                    │
│ Weight 장입률  71%        16%       +55%p                    │
│ 낭비 공간      22 in      28.8 ft   15× 더                   │
├─────────────────────────────────────────────────────────────┤
│ All constraints passed ✓                                    │
│ ✓ All units upright       ✓ Door track cleared              │
│ ✓ Stack limits respected  ✓ Washer/Dryer paired             │
│ ✓ Max lane count          ✓ Under payload                   │
├─────────────────────────────────────────────────────────────┤
│ → Next: 3D load diagram + work order        [View 3D ↗]    │
└─────────────────────────────────────────────────────────────┘
```

### Color tokens
- Recommended truck card: green border (`#1D9E75`)
- Load rate bars: green (≥70%), orange (40–69%), gray (<40%)
- Constraint check icons: green `#0F6E56`

### Data binding

```typescript
interface DecisionScreenProps {
  load: {
    id: string;
    items: Array<{model: string, qty: number, dim_in: [number, number, number], unit_vol_ft3: number, weight_lb: number}>;
    total_volume_ft3: number;
    total_weight_lb: number;
  };
  trucks: Array<{
    type: '26ft' | '53ft';
    name: string;
    dims_ft: [number, number, number];
    payload_lb: number;
    simulation: {
      fits: boolean;
      fitted: number;
      length_used_ft: number;
      volume_loaded_ft3: number;
      load_rates: {length_pct: number, volume_pct: number, weight_pct: number};
      verdict: 'recommended' | 'oversized' | 'too_small';
    }
  }>;
}
```

## Screen 2-A — 3D load diagram (Manager view)

**Goal**: Manager reviews the complete plan in one glance.

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│ [Step 2 of 2] 3D load diagram                               │
│ 26ft Box Truck · 36 units · 24.2 ft used (93%)              │
├─────────────────────────────────────────────────────────────┤
│ Isometric view (all rows, lanes & tiers)                    │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │  Cab ←  · 26 ft ·  → Dock                               │ │
│ │  ┌─────────────────────────────────────────────────┐    │ │
│ │  │ [3D isometric SVG with 36 boxes]                │    │ │
│ │  │ Zone A    Zone B          Zone C    Zone D  ░░  │    │ │
│ │  │ Refrig.   Washer+Dryer   Dishwash.  Oven  free  │    │ │
│ │  │           (paired)                              │    │ │
│ │  └─────────────────────────────────────────────────┘    │ │
│ │  [R] Refrig  [W] Wash  [D] Dry  [Dw] Dish  [O] Oven    │ │
│ └─────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│ Zone breakdown — rows × lanes × tiers                       │
│ Zone · Model              Qty  R×L×T       Length      Weight│
│ A · Refrigerator           6   3×2×1       0→8.9 ft   2,052 │
│ B · Washer+Dryer (paired)  16  3×3×2       8.9→17.0    2,992 │
│ C · Dishwasher             10  2×3×2       17.0→21.8   1,210 │
│ D · Wall Oven              4   1×2×2       21.8→24.2     748 │
│ + Free space               —   22 in       24.2→26.0   112 ft³│
├─────────────────────────────────────────────────────────────┤
│ Load sequence  ① → ② → ③ → ④ → ✓                            │
│ ① Refrigerators (Zone A, front)                             │
│ ② Washers (floor) → Dryers (top) Zone B                     │
│ ③ Dishwashers Zone C                                        │
│ ④ Wall ovens Zone D (rear)                                  │
│ ✓ Secure & inspect                                          │
├─────────────────────────────────────────────────────────────┤
│ [Excel report] [Print work order] [Interactive 3D]          │
└─────────────────────────────────────────────────────────────┘
```

### 3D rendering spec

- **Projection**: Cabinet projection at 30°, scale 0.5 for depth
- **Coordinate transform**:
  ```
  screen_x = origin_x + X * scale + Y * scale * 0.5 * cos(30°)
  screen_y = origin_y - Z * scale - Y * scale * 0.5 * sin(30°)
  ```
- **Each box renders 3 polygons**: front face, top face, right face
- **z-order**: render boxes sorted by `(-Y - depth, X, Z)` so far boxes render first, near boxes overlay
- **Door track**: red semi-transparent box at top-rear (last 5 ft of length, top 10")
- **Free space**: green-tinted dashed box at tail
- **Phase 1**: replace SVG with `@react-three/fiber` for free rotation

### Color palette (LG-friendly)

| Category | Top face | Front face | Right face | Stroke |
|----------|----------|------------|------------|--------|
| Refrigerator | #B5D4F4 | #85B7EB | #378ADD | #0C447C |
| Washer | #F4C0D1 | #ED93B1 | #D4537E | #72243E |
| Dryer | #F4C0D1 | #F4C0D1 | #ED93B1 | #993556 |
| Dishwasher | #CECBF6 | #AFA9EC | #7F77DD | #3C3489 |
| Wall Oven | #F5C4B3 | #F0997B | #D85A30 | #993C1D |

## Screen 2-B — Sequence diagram (Worker view)

**Goal**: Dock worker follows 5 steps. Printed to PDF, taken to the dock.

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│ [Worker view] 장입 순서 가이드                              │
│ 위 3D 뷰와 함께 활용 · 26ft Box Truck · 36 units            │
├─────────────────────────────────────────────────────────────┤
│ 사전 준비 (Pre-load checklist)                              │
│ Tools & people:               Dock lineup order:            │
│ ☐ Hand truck × 2              1. Refrigerator × 6 (closest)  │
│ ☐ Ratchet strap × 4           2. Washer × 8                  │
│ ☐ Moving blanket × 6          3. Dryer × 8                   │
│ ☐ 2 workers                    4. Dishwasher × 10             │
│ ☐ Safety shoes + gloves        5. Wall oven × 4 (furthest)    │
├─────────────────────────────────────────────────────────────┤
│ 장입 5단계                                                  │
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐      │
│ │ ① side │ │ ② side │ │ ③ side │ │ ④ side │ │ ⑤ side │      │
│ │  view  │ │  view  │ │  view  │ │  view  │ │  view  │      │
│ │ [Fridge│ │+Washer │ │+Dryer  │ │+Dish.  │ │+Oven   │      │
│ │  only] │ │ (floor)│ │ (top)  │ │  2-tier│ │  +secure│     │
│ ├────────┤ ├────────┤ ├────────┤ ├────────┤ ├────────┤      │
│ │ Tip    │ │ Tip    │ │ Tip    │ │ Tip    │ │ Tip    │      │
│ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘      │
├─────────────────────────────────────────────────────────────┤
│ ✓ Final secure & inspect                                    │
│ ☐ 4 ratchet straps  ☐ Arrows up  ☐ Track 10"  ☐ Door close │
├─────────────────────────────────────────────────────────────┤
│ Manager: use 3D view above for big picture                  │
│ Worker: use this for step-by-step    [Print PDF]            │
└─────────────────────────────────────────────────────────────┘
```

### Mini side-view spec

Each step card has a small side-view (200 × 75 viewBox):

- Truck outline: dashed gray
- Already-loaded boxes: faded (opacity 0.5) in their category color
- Newly-added boxes: full color, thicker stroke
- Not-yet-loaded zones: dashed gray outline only
- Length marker line at bottom showing exact ft range being filled

### Print/PDF spec

- A4 / Letter portrait, 1 page
- Header: Load ID, truck, date, planner name
- Body: 5 step cards in 2×3 grid + checklists
- Footer: barcode or QR for tracking

## Units throughout

| Internal | UI display |
|----------|------------|
| mm | inches (in) or feet (ft) |
| kg | pounds (lb) |
| m³ | cubic feet (ft³) |

Conversion at display layer only:
```javascript
const mm_to_in = mm => mm / 25.4;
const mm_to_ft = mm => mm / 304.8;
const kg_to_lb = kg => kg * 2.20462;
const m3_to_ft3 = m3 => m3 * 35.3147;
```

## Accessibility

- All icons paired with text labels
- Contrast ratio ≥ 4.5:1 for all text
- Color is never the only signal (icons + labels + colors)
- Touch targets ≥ 44px on mobile

## Phase 1 implementation notes

- Use Tailwind CSS for layout consistency with the mockups
- 3D viewer: `@react-three/fiber` + `OrbitControls`
- PDF: `reportlab` for backend generation, send as blob to frontend
- State: TanStack Query for data, Zustand or context for local UI state
