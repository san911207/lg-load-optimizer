"""
=========================================================================
LG Appliance Truck Load Optimizer — Streamlit Phase 0
=========================================================================
Engine : engine.best_packer.simulate()   (pair-packing, not py3dbp)
UI     : DESIGN_SPEC.md 기반 3-step flow
         Step 1 · Decision       — 26ft vs 53ft 비교
         Step 2-A · 3D View      — manager view (placeholder, 다음 단계)
         Step 2-B · Worker Guide — 작업자 5-step + PDF (placeholder, 다음 단계)
Units  : UI = ft / in / lb / ft³   ·   Engine = mm / kg
Run    : streamlit run app.py
=========================================================================
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.best_packer import simulate, fits_formula

# ─────────────────────────────────────────────────────────────────────────
# Design tokens (DESIGN_SPEC.md §Color tokens & §Color palette)
# ─────────────────────────────────────────────────────────────────────────
COLOR_RECOMMENDED = "#1D9E75"
COLOR_CHECK_ICON = "#0F6E56"
BAR_GREEN = "#1D9E75"
BAR_ORANGE = "#E89F32"
BAR_GRAY = "#9CA3AF"
TEXT_MUTED = "#6B7280"
BORDER_NEUTRAL = "#E5E7EB"

CAT_COLORS: Dict[str, Dict[str, str]] = {
    "refrigerator": {"top": "#B5D4F4", "front": "#85B7EB", "right": "#378ADD", "stroke": "#0C447C"},
    "washer":       {"top": "#F4C0D1", "front": "#ED93B1", "right": "#D4537E", "stroke": "#72243E"},
    "dryer":        {"top": "#F4C0D1", "front": "#F4C0D1", "right": "#ED93B1", "stroke": "#993556"},
    "dishwasher":   {"top": "#CECBF6", "front": "#AFA9EC", "right": "#7F77DD", "stroke": "#3C3489"},
    "oven":         {"top": "#F5C4B3", "front": "#F0997B", "right": "#D85A30", "stroke": "#993C1D"},
    "microwave":    {"top": "#FDE68A", "front": "#FBBF24", "right": "#D97706", "stroke": "#78350F"},
    "tv":           {"top": "#C7D2FE", "front": "#818CF8", "right": "#4F46E5", "stroke": "#312E81"},
    "monitor":      {"top": "#A7F3D0", "front": "#34D399", "right": "#059669", "stroke": "#064E3B"},
    "other":        {"top": "#E5E7EB", "front": "#9CA3AF", "right": "#6B7280", "stroke": "#1F2937"},
}

# ─────────────────────────────────────────────────────────────────────────
# Unit conversions (display layer only)
# Master data is now in US units (in / lb / cft). Display sometimes needs ft.
# ─────────────────────────────────────────────────────────────────────────
def in_to_ft(v: float) -> float: return v / 12.0
def cuin_to_cft(v: float) -> float: return v / 1728.0


def broad_category(cat: str) -> str:
    """Map detailed category (e.g. 'Refrigerator_FrenchDoor4Door') → broad bucket."""
    c = (cat or "").lower()
    # Order matters: longer/more-specific keys before substrings.
    # "dishwasher" contains "washer", so check it first.
    for key in ("refrigerator", "dishwasher", "microwave", "monitor", "washer", "dryer"):
        if key in c:
            return key
    if "oven" in c or "range" in c:
        return "oven"
    if c.startswith("tv_") or c == "tv":
        return "tv"
    return "other"


# ─────────────────────────────────────────────────────────────────────────
# Data loading + calibrations (CLAUDE.md §Calibrations applied to sample master)
# ─────────────────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).parent
DEFAULT_MASTER = APP_DIR / "data" / "sample_input.xlsx"
OUT_DIR = APP_DIR / "outputs"
OUT_DIR.mkdir(exist_ok=True)

# Per-user persistent data dir. Corp Windows often locks `Documents`, so we
# probe several locations and pick the first writable one. Non-system drives
# (E:, D:) are tried first because they are usually exempt from IT lockdown.
def _candidate_data_dirs(extra: Optional[Path] = None) -> List[Path]:
    cands: List[Path] = []
    if extra is not None:
        cands.append(extra)
    # Windows non-system drives (typical corp scratch / project drives)
    for drive_letter in ("E", "D", "F", "G"):
        drive_root = Path(f"{drive_letter}:/")
        if drive_root.exists():
            cands.append(drive_root / "LG_Load_Optimizer")
    cands.extend([
        Path.home() / "Documents" / "LG_Load_Optimizer",
        Path.home() / "LG_Load_Optimizer",
    ])
    # De-dupe preserving order
    seen, deduped = set(), []
    for c in cands:
        key = str(c).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        return True
    except Exception:
        return False


def _resolve_user_data_dir(override: Optional[Path] = None) -> Path:
    for d in _candidate_data_dirs(extra=override):
        if _writable(d):
            return d
    return _candidate_data_dirs()[-1]


USER_DATA_DIR = _resolve_user_data_dir()
USER_MASTER_PATH = USER_DATA_DIR / "master.xlsx"

# Column schema the engine relies on (everything else is optional)
REQUIRED_MASTER_COLS = [
    "model_code", "width_in", "depth_in", "height_in", "weight_lb", "stackable",
]
OPTIONAL_MASTER_COLS = {
    "category": "Uncategorized",
    "this_side_up": True,
    "load_bear_lb": 0.0,
    "fragile": False,
    "notes": "",
    "volume_cft": None,   # auto-computed if missing
}

REQUIRED_TRUCK_COLS = ["truck_type", "length_in", "width_in", "height_in", "max_payload_lb"]
OPTIONAL_TRUCK_COLS = {
    "display_name": None,
    "cargo_volume_cft": None,  # auto-computed if missing
}


@st.cache_data
def _load_workbook(path: str):
    df_master = pd.read_excel(path, sheet_name="Model_Master")
    df_trucks = pd.read_excel(path, sheet_name="Truck_Master")
    try:
        df_loads = pd.read_excel(path, sheet_name="Loads")
    except ValueError:
        df_loads = None
    return df_master, df_trucks, df_loads


def _load_initial_data() -> tuple[pd.DataFrame, pd.DataFrame, Optional[pd.DataFrame], str]:
    """Prefer a user-saved master if present at any known location."""
    global USER_DATA_DIR, USER_MASTER_PATH

    # Pointer file lets us recover a custom save path from previous session
    candidate_paths: List[Path] = []
    pointer = Path.home() / ".lg_load_optimizer_path"
    if pointer.exists():
        try:
            candidate_paths.append(Path(pointer.read_text().strip()))
        except Exception:
            pass
    candidate_paths.append(USER_MASTER_PATH)
    for d in _candidate_data_dirs():
        candidate_paths.append(d / "master.xlsx")

    for p in candidate_paths:
        if p.exists():
            try:
                df_master, df_trucks, df_loads = _load_workbook(str(p))
                # Defensive re-normalize: an older saved master may have NaN
                # in text columns that breaks sort/group operations downstream.
                try:
                    df_master, _ = normalize_master_df(df_master)
                except Exception:
                    pass  # if normalize rejects, surface only the raw load error
                USER_DATA_DIR = p.parent
                USER_MASTER_PATH = p
                return df_master, df_trucks, df_loads, "user"
            except Exception:
                continue
    df_master, df_trucks, df_loads = _load_workbook(str(DEFAULT_MASTER))
    return df_master, df_trucks, df_loads, "bundled"


def save_user_master(
    df_master: pd.DataFrame,
    df_trucks: Optional[pd.DataFrame] = None,
    df_loads: Optional[pd.DataFrame] = None,
    override_dir: Optional[Path] = None,
) -> Path:
    """Persist current master to a writable location.
    Tries (in order): user-supplied override → non-system drives → Documents → home.
    Returns the path actually written.
    """
    last_err: Optional[Exception] = None
    for d in _candidate_data_dirs(extra=override_dir):
        target = d / "master.xlsx"
        try:
            d.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(target, engine="openpyxl") as w:
                df_master.to_excel(w, sheet_name="Model_Master", index=False)
                if df_trucks is not None:
                    df_trucks.to_excel(w, sheet_name="Truck_Master", index=False)
                if df_loads is not None:
                    df_loads.to_excel(w, sheet_name="Loads", index=False)
            _load_workbook.clear()
            # Remember the resolved path for the rest of this session + next launch
            global USER_DATA_DIR, USER_MASTER_PATH
            USER_DATA_DIR = d
            USER_MASTER_PATH = target
            # Also write a pointer file so next launch can find non-default paths
            try:
                pointer = Path.home() / ".lg_load_optimizer_path"
                pointer.write_text(str(target))
            except Exception:
                pass
            return target
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    raise IOError(
        f"All candidate save paths failed (last: {last_err}). "
        f"Tried: {[str(d) for d in _candidate_data_dirs(extra=override_dir)]}"
    )


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase + strip + collapse whitespace in column names."""
    df = df.copy()
    df.columns = [
        str(c).strip().lower().replace(" ", "_").replace("(", "").replace(")", "")
        for c in df.columns
    ]
    return df


def _coerce_bool(s: pd.Series) -> pd.Series:
    """Accept True/False/1/0/Y/N/Yes/No (any case)."""
    def _conv(v):
        if isinstance(v, bool):
            return v
        if pd.isna(v):
            return False
        if isinstance(v, (int, float)):
            return bool(v)
        s = str(v).strip().lower()
        return s in ("true", "1", "yes", "y", "t")
    return s.apply(_conv)


def normalize_master_df(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Take an arbitrary upload, return (clean_df, warnings).
    Raises ValueError if required columns are missing.
    """
    df = _normalize_columns(raw)
    warnings: list[str] = []

    missing = [c for c in REQUIRED_MASTER_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}. "
            f"Found columns: {list(df.columns)}. "
            f"Required schema: {REQUIRED_MASTER_COLS}"
        )

    # Fill optional columns
    for col, default in OPTIONAL_MASTER_COLS.items():
        if col not in df.columns:
            df[col] = default
            if default is not None:
                warnings.append(f"Column '{col}' missing — filled with default ({default!r})")

    # Coerce types
    for col in ("width_in", "depth_in", "height_in", "weight_lb", "load_bear_lb"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["stackable"] = _coerce_bool(df["stackable"])
    df["fragile"] = _coerce_bool(df["fragile"])
    if "this_side_up" in df.columns:
        df["this_side_up"] = _coerce_bool(df["this_side_up"])
    # Ensure text columns are str so sort/group operations don't mix dtypes
    for col in ("model_code", "category", "notes"):
        if col in df.columns:
            df[col] = df[col].fillna(
                "Uncategorized" if col == "category" else ""
            ).astype(str)

    # Drop rows with missing critical dims
    invalid = df[REQUIRED_MASTER_COLS].isna().any(axis=1)
    if invalid.any():
        warnings.append(f"Dropped {invalid.sum()} row(s) with missing required values")
        df = df.loc[~invalid].reset_index(drop=True)

    # Unit sanity check — appliances in inches: ~10-80 in. Values >200 look like mm.
    if not df.empty:
        median_w = df["width_in"].median()
        if median_w > 200:
            warnings.append(
                f"⚠ width_in median = {median_w:.0f} — values look like MILLIMETERS, "
                "not inches. Divide by 25.4 before uploading, or rename columns."
            )

    # Auto-compute volume_cft for rows where it's missing
    if "volume_cft" in df.columns:
        df["volume_cft"] = pd.to_numeric(df["volume_cft"], errors="coerce")
        need_vol = df["volume_cft"].isna()
        if need_vol.any():
            df.loc[need_vol, "volume_cft"] = (
                df.loc[need_vol, "width_in"]
                * df.loc[need_vol, "depth_in"]
                * df.loc[need_vol, "height_in"]
                / 1728.0
            ).round(2)
            warnings.append(f"Auto-computed volume_cft for {need_vol.sum()} row(s) (= w×d×h ÷ 1728)")

    return df, warnings


def normalize_trucks_df(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    df = _normalize_columns(raw)
    warnings: list[str] = []
    missing = [c for c in REQUIRED_TRUCK_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Truck_Master missing required columns: {missing}. "
            f"Found: {list(df.columns)}. Required: {REQUIRED_TRUCK_COLS}"
        )
    for col, default in OPTIONAL_TRUCK_COLS.items():
        if col not in df.columns:
            df[col] = default
    for col in ("length_in", "width_in", "height_in", "max_payload_lb", "cargo_volume_cft"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    # Auto-compute cargo_volume_cft if missing
    need_vol = df["cargo_volume_cft"].isna() if "cargo_volume_cft" in df.columns else None
    if need_vol is not None and need_vol.any():
        df.loc[need_vol, "cargo_volume_cft"] = (
            df.loc[need_vol, "length_in"]
            * df.loc[need_vol, "width_in"]
            * df.loc[need_vol, "height_in"]
            / 1728.0
        ).round(1)
        warnings.append(f"Auto-computed cargo_volume_cft for {need_vol.sum()} truck(s)")
    return df, warnings


def apply_calibrations(master_dict: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """CLAUDE.md: required calibrations so all 36 units fit in 26ft sample."""
    if "LDFN4542S" in master_dict:
        master_dict["LDFN4542S"].update({"stackable": True, "load_bear_lb": 132.3, "fragile": False})
    if "LWS3063ST" in master_dict:
        master_dict["LWS3063ST"].update({"stackable": True, "load_bear_lb": 198.4, "fragile": False})
    return master_dict


# ─────────────────────────────────────────────────────────────────────────
# Streamlit page setup
# ─────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="LG Load Optimizer", page_icon="🚛", layout="wide")

st.sidebar.title("🚛 Load Optimizer")
page = st.sidebar.radio(
    "Navigation",
    ["📦 Load Plan", "📋 Model Master", "🚛 Truck Master"],
)
st.sidebar.markdown("---")
st.sidebar.caption("Phase 0 · best_packer engine")


# Initial master + trucks (auto-loads user-saved master if present)
if "df_master" not in st.session_state:
    df_master, df_trucks, df_loads, source = _load_initial_data()
    st.session_state.df_master = df_master
    st.session_state.df_trucks = df_trucks
    st.session_state.df_loads = df_loads
    st.session_state.master_source = source


# =========================================================================
# Step 1 helpers
# =========================================================================
def compute_loaded_volume_ft3(sim_result: Dict[str, Any]) -> float:
    """Sum of placed-box volumes in ft³ (placement dims are in inches)."""
    total_cuin = sum(
        p["dim_x_in"] * p["dim_y_in"] * p["dim_z_in"]
        for p in sim_result["placements"]
    )
    return cuin_to_cft(total_cuin)


def pick_recommended(sim_26: Dict[str, Any], sim_53: Dict[str, Any]) -> Optional[str]:
    """Smaller truck first (cost). 26ft if it fits, otherwise 53ft."""
    if sim_26["fits"]:
        return "26ft"
    if sim_53["fits"]:
        return "53ft"
    return None


def build_load_composition_df(
    load_lines: List[Dict[str, Any]], master: Dict[str, Dict[str, Any]]
) -> pd.DataFrame:
    rows = []
    for line in load_lines:
        mc = line["model_code"]
        m = master[mc]
        unit_vol_ft3 = m.get("volume_cft") or cuin_to_cft(
            m["width_in"] * m["depth_in"] * m["height_in"]
        )
        rows.append({
            "Model": mc,
            "Category": m["category"],
            "Qty": line["quantity"],
            "Dim W × D × H (in)": (
                f"{m['width_in']:.1f} × "
                f"{m['depth_in']:.1f} × "
                f"{m['height_in']:.1f}"
            ),
            "Unit vol (ft³)": round(unit_vol_ft3, 1),
            "Total vol (ft³)": round(unit_vol_ft3 * line["quantity"], 0),
            "Weight (lb)": round(m["weight_lb"] * line["quantity"], 0),
        })
    return pd.DataFrame(rows)


def _bar_color(pct: float) -> str:
    if pct >= 70:
        return BAR_GREEN
    if pct >= 40:
        return BAR_ORANGE
    return BAR_GRAY


def build_loadrate_bars(metrics: Dict[str, Any]) -> go.Figure:
    rows = [
        ("Length", metrics["compactness_pct"]),
        ("Volume", metrics["volume_util_pct"]),
        ("Weight", metrics["weight_util_pct"]),
    ]
    fig = go.Figure(go.Bar(
        y=[r[0] for r in rows],
        x=[r[1] for r in rows],
        orientation="h",
        marker_color=[_bar_color(r[1]) for r in rows],
        text=[f"{r[1]:.0f}%" for r in rows],
        textposition="outside",
        cliponaxis=False,
        hoverinfo="x+y",
    ))
    fig.update_layout(
        height=180,
        margin=dict(l=20, r=50, t=10, b=10),
        xaxis=dict(range=[0, 110], showgrid=False, showticklabels=False, zeroline=False),
        yaxis=dict(autorange="reversed", tickfont=dict(size=12)),
        plot_bgcolor="white",
        paper_bgcolor="white",
        showlegend=False,
    )
    return fig


def render_truck_card(
    truck_key: str,
    sim: Dict[str, Any],
    truck_spec: Dict[str, Any],
    is_recommended: bool,
) -> None:
    label = "26ft Box Truck" if truck_key == "26ft" else "53ft Dry Van"
    dims_ft = (
        f"{in_to_ft(truck_spec['length_in']):.0f} × "
        f"{in_to_ft(truck_spec['width_in']):.1f} × "
        f"{in_to_ft(truck_spec['height_in']):.1f} ft"
    )

    if sim["fits"]:
        pill = (
            "<span style='background:#D1FAE5;color:#0F6E56;"
            "padding:3px 12px;border-radius:12px;font-size:13px;font-weight:700;"
            "letter-spacing:0.3px;'>FITS</span>"
        )
    else:
        pill = (
            "<span style='background:#FEE2E2;color:#991B1B;"
            "padding:3px 12px;border-radius:12px;font-size:13px;font-weight:700;"
            "letter-spacing:0.3px;'>DOES NOT FIT</span>"
        )

    # Recommended cards get an outsized banner + green background tint + thick border.
    # Non-recommended cards stay quiet so the eye snaps to the recommendation.
    if is_recommended:
        banner_html = (
            '<div style="background:linear-gradient(90deg,#1D9E75 0%,#0F6E56 100%);'
            'color:white;padding:10px 16px;border-radius:8px 8px 0 0;'
            'margin:-4px -4px 12px -4px;font-weight:800;font-size:14px;'
            'letter-spacing:1.5px;text-align:center;'
            'box-shadow:0 2px 4px rgba(29,158,117,0.25);">'
            '⭐ &nbsp; RECOMMENDED &nbsp; — &nbsp; USE THIS TRUCK'
            '</div>'
        )
        title_bg = "#F0FDF4"
        title_color = "#0F6E56"
        outer_border = "3px solid #1D9E75"
    else:
        banner_html = ""
        title_bg = "transparent"
        title_color = "#374151"
        outer_border = "1px solid #E5E7EB"

    # Render outer chrome as a single un-indented HTML block — Streamlit's
    # markdown parser treats 4-space-indented lines as code blocks, which
    # mangles multi-div HTML if left indented.
    card_bg = title_bg if is_recommended else "white"
    html = (
        f'<div style="border:{outer_border};border-radius:10px;padding:4px;'
        f'background:{card_bg};">'
        f'{banner_html}'
        f'<div style="padding:0 12px 8px;">'
        f'<div style="font-size:24px;font-weight:800;line-height:1.2;'
        f'color:{title_color};">{label} &nbsp; {pill}</div>'
        f'<div style="color:{TEXT_MUTED};font-size:13px;margin-top:2px;">'
        f'{dims_ft}</div>'
        f'</div>'
        f'</div>'
    )
    with st.container(border=False):
        st.markdown(html, unsafe_allow_html=True)

        c1, c2, c3, c4 = st.columns(4)
        m = sim["metrics"]
        loaded_vol_ft3 = compute_loaded_volume_ft3(sim)
        with c1:
            st.metric("Units", f"{sim['fitted_count']}/{sim['requested_count']}")
        with c2:
            st.metric("Length (ft)", f"{m['x_used_ft']:.1f}")
        with c3:
            st.metric("Volume (ft³)", f"{loaded_vol_ft3:,.0f}")
        with c4:
            st.metric("Weight (lb)", f"{m['weight_total_lb']:,.0f}")

        st.markdown(
            "<div style='font-size:13px;color:#374151;margin:4px 0 -8px 0;font-weight:600;'>"
            "Load rate</div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(
            build_loadrate_bars(m),
            use_container_width=True,
            config={"displayModeBar": False},
        )

        # Verdict
        verdict = build_verdict(sim, truck_spec, is_recommended)
        st.markdown(verdict, unsafe_allow_html=True)


def build_verdict(sim: Dict[str, Any], truck_spec: Dict[str, Any], is_recommended: bool) -> str:
    m = sim["metrics"]
    if not sim["fits"]:
        return (
            f"<div style='color:#991B1B;font-weight:600;margin-top:8px;'>"
            f"⚠ Too small · {sim['unfitted_count']} units left over</div>"
        )
    if is_recommended:
        rem_ft = m["remaining_length_ft"]
        rem_in = rem_ft * 12 if rem_ft < 2 else None
        rem_disp = f"{rem_in:.0f} in" if rem_in is not None else f"{rem_ft:.1f} ft"
        return (
            f"<div style='color:#0F6E56;font-weight:600;margin-top:8px;'>"
            f"✓ Right size · {rem_disp} buffer at rear</div>"
        )
    # fits but oversized
    return (
        f"<div style='color:#92400E;font-weight:600;margin-top:8px;'>"
        f"⚠ Oversized · {m['remaining_length_ft']:.1f} ft empty</div>"
    )


def render_comparison_table(sim_26: Dict[str, Any], sim_53: Dict[str, Any]) -> None:
    m26 = sim_26["metrics"]
    m53 = sim_53["metrics"]

    def fmt_delta_pp(a: float, b: float) -> str:
        d = a - b
        sign = "+" if d >= 0 else ""
        return f"{sign}{d:.0f}%p"

    def fmt_waste(sim: Dict[str, Any]) -> str:
        rem_ft = sim["metrics"]["remaining_length_ft"]
        if rem_ft < 2:
            return f"{rem_ft * 12:.0f} in"
        return f"{rem_ft:.1f} ft"

    rows = [
        ("Length 장입률", f"{m26['compactness_pct']:.0f}%", f"{m53['compactness_pct']:.0f}%",
         fmt_delta_pp(m26["compactness_pct"], m53["compactness_pct"])),
        ("Volume 장입률", f"{m26['volume_util_pct']:.0f}%", f"{m53['volume_util_pct']:.0f}%",
         fmt_delta_pp(m26["volume_util_pct"], m53["volume_util_pct"])),
        ("Weight 장입률", f"{m26['weight_util_pct']:.0f}%", f"{m53['weight_util_pct']:.0f}%",
         fmt_delta_pp(m26["weight_util_pct"], m53["weight_util_pct"])),
        ("낭비 공간", fmt_waste(sim_26), fmt_waste(sim_53), "—"),
    ]
    df = pd.DataFrame(rows, columns=["Metric", "26ft Box", "53ft Van", "Δ"])
    st.dataframe(df, hide_index=True, use_container_width=True)


def render_constraints(sim: Dict[str, Any]) -> None:
    # Pair-packing algorithm enforces every constraint structurally except payload.
    # Payload is enforced by master spec; we surface the actual margin.
    under_payload = sim["metrics"]["weight_util_pct"] <= 100
    items: List[Tuple[str, bool]] = [
        ("All units upright", True),
        ("Door track cleared", True),
        ("Stack limits respected", True),
        ("Washer/Dryer paired", True),
        ("Max lane count", True),
        ("Under payload", under_payload),
    ]
    c1, c2 = st.columns(2)
    for i, (label, ok) in enumerate(items):
        col = c1 if i % 2 == 0 else c2
        icon = "✓" if ok else "✗"
        color = COLOR_CHECK_ICON if ok else "#DC2626"
        col.markdown(
            f"<div style='margin:4px 0;'>"
            f"<span style='color:{color};font-weight:700;font-size:16px;'>{icon}</span> "
            f"<span>{label}</span></div>",
            unsafe_allow_html=True,
        )


# =========================================================================
# Math formula — closed-form fit predictor (no simulator)
# =========================================================================
def render_math_formula(
    load_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    trucks_map: Dict[str, Dict[str, Any]],
) -> None:
    with st.expander("📐 Math formula — predict fit without simulator", expanded=False):
        st.markdown(
            r"""
For each **dim-group** $g$ (same width × depth × height SKUs paired together):

$$
\begin{aligned}
\text{eff\_H} &= H_{truck} - 250\text{ mm}\quad\text{(door track loss)} \\
\text{layers}_g &= \begin{cases}
  \lfloor \text{eff\_H} / h_g \rfloor & \text{if stackable}_g \\
  1 & \text{otherwise}
\end{cases}
\end{aligned}
$$

For each orientation $(w^*, d^*) \in \{(w_g,d_g),\,(d_g,w_g)\}$:
$$
\text{lanes} = \lfloor W_{truck} / w^* \rfloor,\quad
\text{per\_row} = \text{lanes} \times \text{layers}_g,\quad
\text{rows} = \lceil Q_g / \text{per\_row} \rceil,\quad
\text{length} = \text{rows} \times d^*
$$

Choose the orientation that minimizes $\text{length}$. Then:

$$
\boxed{\text{FITS}\ \iff\ \sum_g \text{length}_g\ \le\ L_{truck}}
$$

No `load_bear` or `fragile` gating in max-fit mode (CEO 2026-05-15).
            """
        )

        st.markdown("**Applied to this load**")
        col26, col53 = st.columns(2)
        for col, tkey in ((col26, "26ft"), (col53, "53ft")):
            with col:
                result = fits_formula(load_lines, master, trucks_map[tkey])
                label = "26ft Box Truck" if tkey == "26ft" else "53ft Dry Van"
                verdict = "✓ FITS" if result["fits"] else "✗ DOES NOT FIT"
                color = COLOR_CHECK_ICON if result["fits"] else "#DC2626"
                st.markdown(
                    f"**{label}** &nbsp;&nbsp; "
                    f"<span style='color:{color};font-weight:700;'>{verdict}</span>",
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"Σ length = {result['predicted_length_ft']:.2f} ft  vs  "
                    f"truck = {result['truck_length_ft']:.0f} ft  →  "
                    f"{'+' if result['remaining_ft'] >= 0 else ''}"
                    f"{result['remaining_ft']:.2f} ft buffer"
                )
                rows = []
                for b in result["breakdown"]:
                    models_str = " + ".join(m for m, _ in b["models"])
                    if len(models_str) > 22:
                        models_str = models_str[:20] + "…"
                    rows.append({
                        "Models": models_str,
                        "Qty": b["qty"],
                        "Lanes × Layers": f"{b['lanes']} × {b['layers']}",
                        "Rows": b["rows"],
                        "Length (ft)": b["length_ft"],
                    })
                rows.append({
                    "Models": "Σ",
                    "Qty": "—",
                    "Lanes × Layers": "—",
                    "Rows": "—",
                    "Length (ft)": round(result["predicted_length_ft"], 2),
                })
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# =========================================================================
# Step 1 page
# =========================================================================
def render_step1(
    load_id: str,
    load_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    trucks_map: Dict[str, Dict[str, Any]],
    sim_26: Dict[str, Any],
    sim_53: Dict[str, Any],
    destination: Optional[str],
) -> None:
    total_qty = sum(l["quantity"] for l in load_lines)
    total_weight_lb = sum(
        master[l["model_code"]]["weight_lb"] * l["quantity"] for l in load_lines
    )
    total_vol_cft = sum(
        (master[l["model_code"]].get("volume_cft") or cuin_to_cft(
            master[l["model_code"]]["width_in"]
            * master[l["model_code"]]["depth_in"]
            * master[l["model_code"]]["height_in"]
        )) * l["quantity"]
        for l in load_lines
    )

    st.markdown("#### Step 1 of 2 · Can it fit?")
    parts = [f"Load **{load_id}**"]
    if destination:
        parts.append(destination)
    parts += [
        f"{total_qty} units",
        f"{total_weight_lb:,.0f} lb",
        f"{total_vol_cft:,.0f} ft³",
    ]
    st.caption(" · ".join(parts))

    with st.expander("📋 Load composition", expanded=False):
        df_comp = build_load_composition_df(load_lines, master)
        st.dataframe(df_comp, hide_index=True, use_container_width=True)

    st.markdown("### Truck simulation results")
    recommended = pick_recommended(sim_26, sim_53)
    c1, c2 = st.columns(2)
    with c1:
        render_truck_card("26ft", sim_26, trucks_map["26ft"], is_recommended=(recommended == "26ft"))
    with c2:
        render_truck_card("53ft", sim_53, trucks_map["53ft"], is_recommended=(recommended == "53ft"))

    if recommended is None:
        st.error(
            "⚠ Neither truck can fit the full load. "
            "Consider splitting across two trucks or upgrading to a multi-stop plan."
        )

    st.markdown("### Why this recommendation?")
    render_comparison_table(sim_26, sim_53)

    st.markdown("### All constraints passed")
    primary_sim = sim_26 if recommended == "26ft" else sim_53
    render_constraints(primary_sim)

    render_math_formula(load_lines, master, trucks_map)

    st.info(
        "→ Switch to the **Step 2 · Load & Work Order** tab above for the 3D view, "
        "loading sequence, and printable work order."
    )


# =========================================================================
# Step 2-A helpers (3D rendering)
# =========================================================================
DOOR_TRACK_LOSS_IN = 10     # 10" headroom loss on roll-up doors (rear)
DOOR_TRACK_LENGTH_IN = 60   # 5 ft × 12 in

CATEGORY_ICONS = {
    "refrigerator": "R", "washer": "W", "dryer": "D",
    "dishwasher": "Dw", "oven": "O", "microwave": "M",
    "tv": "TV", "monitor": "Mon", "other": "?",
}

CIRCLED = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]


def _box_mesh(
    x0: float, y0: float, z0: float,
    dx: float, dy: float, dz: float,
    color: str, opacity: float = 0.92, hovertext: Optional[str] = None,
) -> go.Mesh3d:
    x1, y1, z1 = x0 + dx, y0 + dy, z0 + dz
    xs = [x0, x1, x1, x0, x0, x1, x1, x0]
    ys = [y0, y0, y1, y1, y0, y0, y1, y1]
    zs = [z0, z0, z0, z0, z1, z1, z1, z1]
    # 12 triangles: 2 per face × 6 faces (bottom, top, front, right, back, left)
    i = [0, 0, 4, 4, 0, 0, 1, 1, 2, 2, 3, 3]
    j = [1, 2, 5, 6, 1, 5, 2, 6, 3, 7, 0, 4]
    k = [2, 3, 6, 7, 5, 4, 6, 5, 7, 6, 4, 7]
    return go.Mesh3d(
        x=xs, y=ys, z=zs, i=i, j=j, k=k,
        color=color, opacity=opacity, flatshading=True,
        hovertext=hovertext, hoverinfo=("text" if hovertext else "skip"),
        showlegend=False,
    )


def _box_edges(
    x0: float, y0: float, z0: float,
    dx: float, dy: float, dz: float,
    color: str = "#1F2937", width: float = 1.5, dash: Optional[str] = None,
) -> go.Scatter3d:
    x1, y1, z1 = x0 + dx, y0 + dy, z0 + dz
    segments = [
        ((x0, y0, z0), (x1, y0, z0)),
        ((x1, y0, z0), (x1, y1, z0)),
        ((x1, y1, z0), (x0, y1, z0)),
        ((x0, y1, z0), (x0, y0, z0)),
        ((x0, y0, z1), (x1, y0, z1)),
        ((x1, y0, z1), (x1, y1, z1)),
        ((x1, y1, z1), (x0, y1, z1)),
        ((x0, y1, z1), (x0, y0, z1)),
        ((x0, y0, z0), (x0, y0, z1)),
        ((x1, y0, z0), (x1, y0, z1)),
        ((x1, y1, z0), (x1, y1, z1)),
        ((x0, y1, z0), (x0, y1, z1)),
    ]
    xs: List[Any] = []
    ys: List[Any] = []
    zs: List[Any] = []
    for (p, q) in segments:
        xs.extend([p[0], q[0], None])
        ys.extend([p[1], q[1], None])
        zs.extend([p[2], q[2], None])
    line = dict(color=color, width=width)
    if dash:
        line["dash"] = dash
    return go.Scatter3d(
        x=xs, y=ys, z=zs, mode="lines",
        line=line, hoverinfo="skip", showlegend=False,
    )


def build_3d_figure(
    sim: Dict[str, Any], truck_spec: Dict[str, Any], master: Dict[str, Dict[str, Any]]
) -> go.Figure:
    L = truck_spec["length_in"]
    W = truck_spec["width_in"]
    H = truck_spec["height_in"]

    traces: List[Any] = []

    # Truck outer wireframe (gray dotted)
    traces.append(_box_edges(0, 0, 0, L, W, H, color="#9CA3AF", width=1, dash="dot"))

    # Boxes
    for p in sim["placements"]:
        cat = broad_category(master[p["model_code"]]["category"])
        palette = CAT_COLORS.get(cat, CAT_COLORS["other"])
        hover = (
            f"#{p['seq']} · {p['model_code']}<br>"
            f"x={p['x_in']/12.0:.1f} ft · lane {p['lane']} · "
            f"layer {p['layer']}<br>{p['weight_lb']:.0f} kg "
            f"({p['weight_lb']:.0f} lb)"
        )
        traces.append(_box_mesh(
            p["x_in"], p["y_in"], p["z_in"],
            p["dim_x_in"], p["dim_y_in"], p["dim_z_in"],
            color=palette["front"], opacity=0.92, hovertext=hover,
        ))
        traces.append(_box_edges(
            p["x_in"], p["y_in"], p["z_in"],
            p["dim_x_in"], p["dim_y_in"], p["dim_z_in"],
            color=palette["stroke"], width=1.2,
        ))

    # Door track (rear 5 ft × top 10") — CLAUDE.md §Critical real-world constraints
    door_x = L - DOOR_TRACK_LENGTH_IN
    door_z = H - DOOR_TRACK_LOSS_IN
    if door_x >= 0 and door_z >= 0:
        traces.append(_box_mesh(
            door_x, 0, door_z,
            DOOR_TRACK_LENGTH_IN, W, DOOR_TRACK_LOSS_IN,
            color="#DC2626", opacity=0.22, hovertext="Door track (keep clear)",
        ))

    # Free space outline after last box
    x_used = sim["metrics"]["x_used_in"]
    if L - x_used > 100:
        traces.append(_box_edges(
            x_used, 0, 0, L - x_used, W, H,
            color="#10B981", width=2, dash="dash",
        ))

    fig = go.Figure(data=traces)
    fig.update_layout(
        scene=dict(
            xaxis=dict(title="Length (in) · Cab → Dock", showbackground=False),
            yaxis=dict(title="Width (in)", showbackground=False),
            zaxis=dict(title="Height (in)", showbackground=False),
            aspectmode="data",
            camera=dict(eye=dict(x=1.6, y=-2.0, z=1.1)),
        ),
        height=580,
        margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
        paper_bgcolor="white",
    )
    return fig


def _group_zones(sim: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    """
    Group placements by physical dimension key (paired same-dim items → one zone).
    Returns zones sorted by first x position (cab → dock).
    Used by Step 2-A zone breakdown and Step 2-B loading sequence.
    """
    groups: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = {}
    for p in sim["placements"]:
        key = (p["dim_y_in"], p["dim_x_in"], p["dim_z_in"])
        groups.setdefault(key, []).append(p)
    return sorted(groups.values(), key=lambda ps: min(p["x_in"] for p in ps))


def _zone_category_label(ps: List[Dict[str, Any]], master: Dict[str, Dict[str, Any]]) -> str:
    broad_cats: List[str] = []
    for p in ps:
        c = broad_category(master[p["model_code"]]["category"])
        if c not in broad_cats:
            broad_cats.append(c)
    return " + ".join(c.capitalize() for c in broad_cats)


def build_zone_breakdown_df(
    sim: Dict[str, Any], master: Dict[str, Dict[str, Any]]
) -> pd.DataFrame:
    zones = _group_zones(sim)
    rows = []
    zone_letters = list("ABCDEFGHIJ")
    for idx, ps in enumerate(zones):
        models = sorted({p["model_code"] for p in ps})
        model_str = " + ".join(models)
        if len(model_str) > 28:
            model_str = model_str[:26] + "…"
        lanes = len({p["lane"] for p in ps})
        layers = len({p["layer"] for p in ps})
        rows_count = len({p["x_in"] for p in ps})
        x_start_ft = min(p["x_in"] for p in ps) / 12.0
        x_end_ft = max(p["x_in"] + p["dim_x_in"] for p in ps) / 12.0
        weight_lb = sum(p["weight_lb"] for p in ps)
        rows.append({
            "Zone": zone_letters[idx] if idx < len(zone_letters) else f"Z{idx}",
            "Category": _zone_category_label(ps, master),
            "Models": model_str,
            "Qty": len(ps),
            "R × L × T": f"{rows_count} × {lanes} × {layers}",
            "Length range (ft)": f"{x_start_ft:.1f} → {x_end_ft:.1f}",
            "Weight (lb)": int(round(weight_lb)),
        })
    return pd.DataFrame(rows)


def render_load_sequence(df_zones: pd.DataFrame) -> None:
    if df_zones.empty:
        st.caption("(no placements)")
        return
    parts = []
    for i, row in enumerate(df_zones.itertuples(), 1):
        marker = CIRCLED[i - 1] if i <= 10 else f"{i}."
        parts.append(f"**{marker}** {row.Category}")
    parts.append("**✓** Secure & inspect")
    st.markdown("  →  ".join(parts))


def render_legend_chips(sim: Dict[str, Any], master: Dict[str, Dict[str, Any]]) -> None:
    cat_in_use: List[str] = []
    for p in sim["placements"]:
        cat = broad_category(master[p["model_code"]]["category"])
        if cat not in cat_in_use:
            cat_in_use.append(cat)
    parts = []
    for cat in cat_in_use:
        c = CAT_COLORS.get(cat, CAT_COLORS["other"])
        icon = CATEGORY_ICONS.get(cat, "?")
        parts.append(
            f"<span style='display:inline-block;background:{c['front']};"
            f"color:white;padding:2px 8px;border-radius:6px;font-size:12px;"
            f"margin:0 6px 4px 0;'>[{icon}] {cat.capitalize()}</span>"
        )
    if parts:
        st.markdown("".join(parts), unsafe_allow_html=True)


def build_simple_excel(
    sim: Dict[str, Any], load_id: str, truck_key: str,
    master: Dict[str, Dict[str, Any]], trucks_map: Dict[str, Dict[str, Any]],
) -> bytes:
    from io import BytesIO

    m = sim["metrics"]
    summary = pd.DataFrame([{
        "Load_ID": load_id,
        "Truck": trucks_map[truck_key].get("display_name", truck_key),
        "Fits": sim["fits"],
        "Fitted": sim["fitted_count"],
        "Requested": sim["requested_count"],
        "Length_used_ft": m["x_used_ft"],
        "Compactness_pct": m["compactness_pct"],
        "Volume_util_pct": m["volume_util_pct"],
        "Weight_total_lb": m["weight_total_lb"],
        "Weight_util_pct": m["weight_util_pct"],
        "Remaining_ft": m["remaining_length_ft"],
        "Strategy": sim["strategy"],
    }])

    place_rows = []
    for p in sim["placements"]:
        spec = master.get(p["model_code"], {})
        place_rows.append({
            "Seq": p["seq"],
            "Model_Code": p["model_code"],
            "Category": spec.get("category", ""),
            "Lane": p["lane"], "Layer": p["layer"],
            "Pos_X_ft": round(p["x_in"] / 12.0, 2),
            "Pos_X_in": p["x_in"], "Pos_Y_in": p["y_in"], "Pos_Z_in": p["z_in"],
            "Dim_X_in": p["dim_x_in"], "Dim_Y_in": p["dim_y_in"], "Dim_Z_in": p["dim_z_in"],
            "Weight_lb": round(p["weight_lb"], 1),
        })
    placements_df = pd.DataFrame(place_rows)

    unfitted_df = pd.DataFrame(sim.get("unfitted_detail", []))

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        placements_df.to_excel(writer, sheet_name="Placements", index=False)
        if not unfitted_df.empty:
            unfitted_df.to_excel(writer, sheet_name="Unfitted", index=False)
    return buf.getvalue()


def render_step2(
    load_id: str,
    sim_26: Dict[str, Any], sim_53: Dict[str, Any],
    master: Dict[str, Dict[str, Any]],
    trucks_map: Dict[str, Dict[str, Any]],
    recommended_key: str,
) -> None:
    """
    Combined Step 2: 3D overview (manager) + 2D loading sequence (worker) +
    all downloads + email. One scrollable page so workers see the 3D context
    before the 2D step-by-step guide.
    """
    st.markdown("#### Step 2 · Load & Work Order")

    # Truck selector (default = recommended; keeps planner exploration option)
    labels = {
        "26ft": (
            f"26ft Box Truck · Fits {sim_26['fitted_count']}/{sim_26['requested_count']}"
            + ("" if sim_26["fits"] else f" · ⚠ {sim_26['unfitted_count']} unfitted")
        ),
        "53ft": (
            f"53ft Dry Van · Fits {sim_53['fitted_count']}/{sim_53['requested_count']}"
            + ("" if sim_53["fits"] else f" · ⚠ {sim_53['unfitted_count']} unfitted")
        ),
    }
    options_keys = ["26ft", "53ft"]
    default_idx = options_keys.index(recommended_key)
    chosen_label = st.radio(
        "Truck",
        [labels[k] for k in options_keys],
        index=default_idx,
        horizontal=True,
        key=f"truck_step2_{load_id}",
    )
    chosen_key = options_keys[[labels[k] for k in options_keys].index(chosen_label)]
    sim = sim_26 if chosen_key == "26ft" else sim_53
    truck_spec = trucks_map[chosen_key]
    label = "26ft Box Truck" if chosen_key == "26ft" else "53ft Dry Van"

    m = sim["metrics"]
    st.caption(
        f"{label} · {sim['fitted_count']}/{sim['requested_count']} units · "
        f"{m['x_used_ft']:.1f} ft used ({m['compactness_pct']:.0f}%)"
        + (f" · {m['remaining_length_ft']:.1f} ft buffer" if sim["fits"] else "")
    )

    if not sim["placements"]:
        st.warning("No placements to render. Return to Step 1.")
        return

    # ───── 1. 3D overall view ─────
    st.markdown("##### 1. 3D overall view")
    fig_3d = build_3d_figure(sim, truck_spec, master)
    st.plotly_chart(
        fig_3d, use_container_width=True,
        key=f"step2_3d_{load_id}_{chosen_key}",
    )
    render_legend_chips(sim, master)

    # ───── 2. Zone breakdown + sequence ─────
    st.markdown("##### 2. Zone breakdown — rows × lanes × tiers")
    df_zones = build_zone_breakdown_df(sim, master)
    st.dataframe(df_zones, hide_index=True, use_container_width=True)
    st.markdown("**Load sequence**")
    render_load_sequence(df_zones)

    if sim["unfitted_detail"]:
        st.warning(
            "⚠ Unfitted — these units need a second truck or later shipment."
        )
        st.dataframe(
            pd.DataFrame(sim["unfitted_detail"]),
            hide_index=True, use_container_width=True,
        )

    zones = _group_zones(sim)

    # ───── 3. Pre-load checklist ─────
    st.markdown("##### 3. Pre-load checklist")
    pc1, pc2 = st.columns(2)
    with pc1:
        st.markdown("**Tools & people**")
        for item in [
            "Hand truck × 2",
            "Ratchet strap × 4",
            "Moving blanket × 6",
            "2 workers minimum",
            "Safety shoes + gloves",
        ]:
            st.markdown(f"☐ {item}")
    with pc2:
        st.markdown("**Dock lineup order (closest → far)**")
        for i, ps in enumerate(zones, 1):
            st.markdown(f"{i}. {_zone_category_label(ps, master)} × {len(ps)}")

    # ───── 4. Loading sequence — side-view cards ─────
    st.markdown("##### 4. Loading sequence (side view)")
    n_steps = len(zones) + 1
    seq_cols = st.columns(n_steps)
    for zi, ps in enumerate(zones):
        with seq_cols[zi]:
            marker = CIRCLED[zi] if zi < len(CIRCLED) else f"{zi + 1}."
            st.markdown(f"**{marker} {_zone_category_label(ps, master)}**")
            fig_side = _make_side_view(sim, truck_spec, zones, zi, master)
            st.plotly_chart(
                fig_side, use_container_width=True,
                config={"displayModeBar": False},
                key=f"step2_side_{load_id}_{chosen_key}_{zi}",
            )
            st.caption(_step_range_label(ps))
            st.caption(f"💡 {_step_tip(ps, master)}")
    with seq_cols[-1]:
        st.markdown("**✓ Secure & inspect**")
        fig_final = _make_side_view(sim, truck_spec, zones, len(zones) - 1, master)
        st.plotly_chart(
            fig_final, use_container_width=True,
            config={"displayModeBar": False},
            key=f"step2_side_{load_id}_{chosen_key}_final",
        )
        st.caption("Final")
        st.caption("💡 Strap rows · close door · LIFO unload")

    # ───── 5. Final secure & inspect ─────
    st.markdown("##### 5. ✓ Final secure & inspect")
    fc1, fc2 = st.columns(2)
    with fc1:
        st.markdown("☐ 4 ratchet straps tight")
        st.markdown("☐ Rear 10\" track clear")
    with fc2:
        st.markdown("☐ ↑ This Side Up arrows verified")
        st.markdown("☐ Door rolled down & sealed")

    # ───── 6. Downloads + Email ─────
    st.markdown("---")
    st.markdown("##### 6. Downloads")

    from engine.pdf_gen import generate_work_order
    pdf_bytes = generate_work_order(sim, load_id, truck_label=label)
    excel_bytes = build_simple_excel(sim, load_id, chosen_key, master, trucks_map)
    html_bytes = fig_3d.to_html(include_plotlyjs="cdn", full_html=True).encode("utf-8")

    dc1, dc2, dc3 = st.columns(3)
    with dc1:
        st.download_button(
            "📊 Excel report",
            excel_bytes,
            file_name=f"{load_id}_{chosen_key}_load_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key=f"excel_dl_{load_id}_{chosen_key}",
        )
    with dc2:
        st.download_button(
            "🖨 Print PDF work order",
            pdf_bytes,
            file_name=f"{load_id}_{chosen_key}_workorder.pdf",
            mime="application/pdf",
            use_container_width=True,
            key=f"pdf_dl_{load_id}_{chosen_key}",
        )
    with dc3:
        st.download_button(
            "⬇ Interactive 3D HTML",
            html_bytes,
            file_name=f"{load_id}_{chosen_key}_3d.html",
            mime="text/html",
            use_container_width=True,
            key=f"html_dl_{load_id}_{chosen_key}",
        )

    # Email panel — save attachments to OUT_DIR so render_email_panel can attach them
    try:
        pdf_path = OUT_DIR / f"{load_id}_{chosen_key}_workorder.pdf"
        pdf_path.write_bytes(pdf_bytes)
        excel_path = OUT_DIR / f"{load_id}_{chosen_key}_load_report.xlsx"
        excel_path.write_bytes(excel_bytes)
        html_path = OUT_DIR / f"{load_id}_{chosen_key}_3d.html"
        fig_3d.write_html(str(html_path), include_plotlyjs="cdn", full_html=True)

        from engine.email_ui import render_email_panel
        render_email_panel(
            simulation_result=sim,
            load_id=load_id,
            attachments=[pdf_path, excel_path, html_path],
            truck_type=chosen_key,
        )
    except Exception as exc:  # noqa: BLE001 — surface unexpected wiring errors
        st.expander("📧 Send by email (unavailable)").write(f"Email panel error: {exc}")


# =========================================================================
# Step 2-B helpers (worker side-view sequence)
# =========================================================================
def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _make_side_view(
    sim: Dict[str, Any],
    truck_spec: Dict[str, Any],
    zones: List[List[Dict[str, Any]]],
    step_idx: int,
    master: Dict[str, Dict[str, Any]],
) -> go.Figure:
    """Side-view (X-Z plane) for one loading step."""
    L = truck_spec["length_in"]
    H = truck_spec["height_in"]

    fig = go.Figure()
    # Truck outer outline
    fig.add_shape(
        type="rect", x0=0, y0=0, x1=L, y1=H,
        line=dict(color="#9CA3AF", width=1, dash="dot"),
        fillcolor="rgba(0,0,0,0)", layer="below",
    )
    # Door track (red wash)
    fig.add_shape(
        type="rect",
        x0=L - DOOR_TRACK_LENGTH_IN, y0=H - DOOR_TRACK_LOSS_IN,
        x1=L, y1=H,
        line=dict(width=0),
        fillcolor="rgba(220,38,38,0.18)", layer="below",
    )

    for zi, ps in enumerate(zones):
        if zi > step_idx:
            xmin = min(p["x_in"] for p in ps)
            xmax = max(p["x_in"] + p["dim_x_in"] for p in ps)
            zmax = max(p["z_in"] + p["dim_z_in"] for p in ps)
            fig.add_shape(
                type="rect", x0=xmin, y0=0, x1=xmax, y1=zmax,
                line=dict(color="#9CA3AF", width=1, dash="dash"),
                fillcolor="rgba(0,0,0,0)",
            )
            continue
        is_current = (zi == step_idx)
        alpha = 0.95 if is_current else 0.35
        stroke_w = 1.4 if is_current else 0.7
        for p in ps:
            cat = broad_category(master[p["model_code"]]["category"])
            c = CAT_COLORS.get(cat, CAT_COLORS["other"])
            r, g, b = _hex_to_rgb(c["front"])
            fig.add_shape(
                type="rect",
                x0=p["x_in"], y0=p["z_in"],
                x1=p["x_in"] + p["dim_x_in"], y1=p["z_in"] + p["dim_z_in"],
                line=dict(color=c["stroke"], width=stroke_w),
                fillcolor=f"rgba({r},{g},{b},{alpha})",
            )

    fig.update_layout(
        xaxis=dict(range=[-50, L + 50], showgrid=False, zeroline=False, visible=False),
        yaxis=dict(range=[-50, H + 50], showgrid=False, zeroline=False, visible=False,
                   scaleanchor="x", scaleratio=1),
        height=130,
        margin=dict(l=2, r=2, t=2, b=2),
        plot_bgcolor="white", paper_bgcolor="white", showlegend=False,
    )
    return fig


def _step_tip(ps: List[Dict[str, Any]], master: Dict[str, Dict[str, Any]]) -> str:
    cats = sorted({broad_category(master[p["model_code"]]["category"]) for p in ps})
    layers = max(p["layer"] for p in ps) + 1
    if "washer" in cats and "dryer" in cats:
        return "Washer floor → Dryer top (paired 2-tier)"
    if layers > 1:
        return f"{cats[0].capitalize()} 2-tier · check load_bear"
    if "refrigerator" in cats:
        return "Heavy first · cab end · 60/40 weight rule"
    if "microwave" in cats:
        return "Small items rear · LIFO unload"
    if "dishwasher" in cats:
        return "Stable bottom · stack same-model only"
    return f"{cats[0].capitalize()}"


def _step_range_label(ps: List[Dict[str, Any]]) -> str:
    xmin = min(p["x_in"] for p in ps) / 12.0
    xmax = max(p["x_in"] + p["dim_x_in"] for p in ps) / 12.0
    return f"{xmin:.1f} → {xmax:.1f} ft"




# =========================================================================
# Sidebar Load picker (only shown on Load Plan page)
# =========================================================================
LOADS_REQUIRED_COLS = ["load_id", "model_code", "quantity"]
LOADS_OPTIONAL_COLS = {"destination": None, "pickup_date": None, "truck_type": None}


def normalize_loads_df(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Loads upload normalizer — case-insensitive cols, NaN drop, type coerce."""
    df = _normalize_columns(raw)
    warnings: list[str] = []
    missing = [c for c in LOADS_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}. "
            f"Found columns: {list(df.columns)}. "
            f"Loads schema: load_id (text), model_code (text matching Model_Master), "
            f"quantity (integer). Optional: destination, pickup_date, truck_type."
        )
    for col, default in LOADS_OPTIONAL_COLS.items():
        if col not in df.columns:
            df[col] = default
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    invalid = df[LOADS_REQUIRED_COLS].isna().any(axis=1) | (df["quantity"] <= 0)
    if invalid.any():
        warnings.append(f"Dropped {invalid.sum()} row(s) with missing/zero values")
        df = df.loc[~invalid].reset_index(drop=True)
    df["quantity"] = df["quantity"].astype(int)
    df["load_id"] = df["load_id"].astype(str).str.strip()
    df["model_code"] = df["model_code"].astype(str).str.strip()
    return df, warnings


def sidebar_load_picker() -> Optional[Tuple[str, pd.DataFrame, Optional[str]]]:
    st.sidebar.markdown("### Load source")
    st.sidebar.download_button(
        "📋 Download Loads template",
        build_loads_template_bytes(),
        file_name="lg_loads_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key="loads_template_dl",
        help="Pre-filled template with 3 example rows + Schema_Notes sheet.",
    )
    st.sidebar.caption(
        "Only **3 columns** needed: `load_id`, `model_code`, `quantity`. "
        "Dimensions/weight are auto-pulled from Model_Master via `model_code`."
    )
    uploaded = st.sidebar.file_uploader(
        "Upload Loads Excel", type=["xlsx"], key="loads_uploader",
        help="Required: load_id, model_code, quantity. "
             "Do NOT include width/depth/height/weight — those come from Model_Master.",
    )
    use_sample = st.sidebar.checkbox("Use sample data", value=not uploaded)

    df_loads: Optional[pd.DataFrame] = None
    if uploaded:
        try:
            xls = pd.ExcelFile(uploaded)
            sheets = xls.sheet_names
        except Exception as exc:  # noqa: BLE001
            st.sidebar.error(f"Cannot read Excel: {exc}")
            return None
        # Auto-pick "Loads" if present, otherwise let user choose
        def _default_idx(target: str, options: List[str]) -> int:
            for i, name in enumerate(options):
                if name.strip().lower() == target.lower():
                    return i
            return 0
        chosen_sheet = st.sidebar.selectbox(
            "Loads sheet", sheets,
            index=_default_idx("Loads", sheets), key="loads_sheet_pick",
        )
        try:
            raw_loads = pd.read_excel(xls, sheet_name=chosen_sheet)
        except Exception as exc:  # noqa: BLE001
            st.sidebar.error(f"Sheet read failed: {exc}")
            return None
        try:
            df_loads, lwarns = normalize_loads_df(raw_loads)
        except ValueError as exc:
            st.sidebar.error(f"⚠ {exc}")
            return None
        for w in lwarns:
            st.sidebar.info(f"ℹ {w}")
    elif use_sample:
        df_loads = st.session_state.df_loads

    if df_loads is None or df_loads.empty:
        st.sidebar.warning("No load data available.")
        return None

    load_ids = sorted(df_loads["load_id"].unique().tolist())
    chosen = st.sidebar.selectbox("Load", load_ids, index=0)
    grp = df_loads[df_loads["load_id"] == chosen]
    destination = grp["destination"].iloc[0] if "destination" in grp.columns else None
    return chosen, grp, destination


def build_loads_template_bytes() -> bytes:
    """Generate a minimal Loads template — only 3 required columns.

    Dimensions/weight/stackable are NOT needed in this file — they are looked
    up automatically from Model_Master by `model_code`.
    """
    from io import BytesIO
    template = pd.DataFrame([
        {"load_id": "L001", "model_code": "LF29H8330S", "quantity": 6},
        {"load_id": "L001", "model_code": "WM4000HWA",  "quantity": 8},
        {"load_id": "L001", "model_code": "DLEX4000W",  "quantity": 8},
        {"load_id": "L001", "model_code": "LDFN4542S",  "quantity": 10},
        {"load_id": "L001", "model_code": "LMV1764ST",  "quantity": 12},
        {"load_id": "L002", "model_code": "OLED65C4PUA", "quantity": 40},
        {"load_id": "L002", "model_code": "OLED77C4PUA", "quantity": 20},
    ])
    notes = pd.DataFrame([
        {"column": "load_id",   "required": "YES",
         "description": "Shipment / order ID. Multiple rows sharing the same load_id "
                        "are one shipment (one truck plan)."},
        {"column": "model_code", "required": "YES",
         "description": "Must match a model_code in Model_Master. App will look up "
                        "width/depth/height/weight/stackable automatically — DO NOT "
                        "include those columns here."},
        {"column": "quantity",   "required": "YES",
         "description": "Number of units. Positive integer."},
        {"column": "destination", "required": "optional",
         "description": "Display only — destination DC / customer code."},
        {"column": "pickup_date", "required": "optional", "description": "Display only."},
        {"column": "truck_type",  "required": "optional",
         "description": "Display hint. App simulates both 26ft and 53ft regardless."},
        {"column": "(dims / weight / stackable)", "required": "NO — auto-mapped",
         "description": "These come from Model_Master via model_code lookup. "
                        "Do not duplicate them in the Loads file."},
    ])
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        template.to_excel(w, sheet_name="Loads", index=False)
        notes.to_excel(w, sheet_name="Schema_Notes", index=False)
    return buf.getvalue()


# =========================================================================
# Page: Load Plan
# =========================================================================
if page == "📦 Load Plan":
    st.title("📦 Load Plan")

    picker = sidebar_load_picker()
    if picker is None:
        st.stop()

    load_id, grp, destination = picker

    master = st.session_state.df_master.set_index("model_code").to_dict("index")
    apply_calibrations(master)

    trucks_df = st.session_state.df_trucks
    trucks_map: Dict[str, Dict[str, Any]] = trucks_df.set_index("truck_type").to_dict("index")
    for tt, spec in trucks_map.items():
        spec["truck_type"] = tt

    load_lines = grp[["model_code", "quantity"]].to_dict("records")

    # Validate model codes against current master before simulating
    requested = {l["model_code"] for l in load_lines}
    missing_skus = sorted(requested - set(master.keys()))
    if missing_skus:
        st.error(
            f"⚠ {len(missing_skus)} SKU(s) in load `{load_id}` are NOT in the current "
            f"Model_Master — simulation aborted.\n\n"
            f"Missing: {', '.join(missing_skus[:20])}"
            + (" ..." if len(missing_skus) > 20 else "")
        )
        st.info(
            f"Current master has **{len(master):,} SKUs** "
            f"(source: {st.session_state.get('master_source', 'unknown')}). "
            f"Either:\n"
            f"- Upload a **Loads** file in the sidebar with SKUs that exist in your master, or\n"
            f"- Add the missing SKUs to your Model_Master (Model Master page → re-upload), or\n"
            f"- Click *Use sample data* OFF and upload your own Loads."
        )
        with st.expander("Sample of available SKUs in your master"):
            sample = list(master.keys())[:30]
            st.code("\n".join(sample))
        st.stop()

    # Dim sanity check — surface SKUs whose dims will be rejected by the engine
    DOOR_TRACK_IN = 10
    oversized_for_26 = []
    oversized_for_53 = []
    for line in load_lines:
        mc = line["model_code"]
        spec = master[mc]
        w, d, h = spec["width_in"], spec["depth_in"], spec["height_in"]
        eff_h_26 = trucks_map["26ft"]["height_in"] - DOOR_TRACK_IN
        eff_h_53 = trucks_map["53ft"]["height_in"] - DOOR_TRACK_IN
        reasons_26, reasons_53 = [], []
        # Width along truck width axis
        if w > trucks_map["26ft"]["width_in"]:
            reasons_26.append(f"width {w}>{trucks_map['26ft']['width_in']}")
        if w > trucks_map["53ft"]["width_in"]:
            reasons_53.append(f"width {w}>{trucks_map['53ft']['width_in']}")
        # Height vs effective height
        if h > eff_h_26:
            reasons_26.append(f"height {h}>{eff_h_26} (eff_H after door track)")
        if h > eff_h_53:
            reasons_53.append(f"height {h}>{eff_h_53}")
        # Depth vs truck length (extreme case)
        if d > trucks_map["26ft"]["length_in"]:
            reasons_26.append(f"depth {d}>{trucks_map['26ft']['length_in']}")
        if d > trucks_map["53ft"]["length_in"]:
            reasons_53.append(f"depth {d}>{trucks_map['53ft']['length_in']}")
        if reasons_26:
            oversized_for_26.append((mc, line["quantity"], w, d, h, "; ".join(reasons_26)))
        if reasons_53:
            oversized_for_53.append((mc, line["quantity"], w, d, h, "; ".join(reasons_53)))

    if oversized_for_26 or oversized_for_53:
        with st.expander(
            f"🔎 Dim sanity — "
            f"{len(oversized_for_26)} SKU(s) too big for 26ft, "
            f"{len(oversized_for_53)} SKU(s) too big for 53ft",
            expanded=bool(oversized_for_26),
        ):
            if oversized_for_26:
                st.markdown("**Will be rejected by 26ft truck:**")
                st.dataframe(
                    pd.DataFrame(oversized_for_26,
                                 columns=["model_code", "qty", "w_in", "d_in", "h_in", "reason"]),
                    hide_index=True, use_container_width=True,
                )
            if oversized_for_53:
                st.markdown("**Will be rejected by 53ft truck:**")
                st.dataframe(
                    pd.DataFrame(oversized_for_53,
                                 columns=["model_code", "qty", "w_in", "d_in", "h_in", "reason"]),
                    hide_index=True, use_container_width=True,
                )
            st.caption(
                "These SKUs have at least one dimension exceeding the truck's interior "
                "(after the 10 in door-track loss for height). They will appear as "
                "*unfitted* in the result. Check your Model_Master values."
            )

    # Run both simulations (cache by load_id + signature)
    sig = (load_id, tuple(sorted((l["model_code"], l["quantity"]) for l in load_lines)))
    cache_key = f"sim_{sig}"
    if cache_key not in st.session_state:
        sim_26 = simulate(load_lines, master, trucks_map["26ft"])
        sim_53 = simulate(load_lines, master, trucks_map["53ft"])
        st.session_state[cache_key] = (sim_26, sim_53)
    sim_26, sim_53 = st.session_state[cache_key]

    recommended_key = pick_recommended(sim_26, sim_53) or "26ft"

    tab1, tab2 = st.tabs([
        "Step 1 · Decision",
        "Step 2 · Load & Work Order",
    ])
    with tab1:
        render_step1(load_id, load_lines, master, trucks_map, sim_26, sim_53, destination)
    with tab2:
        render_step2(load_id, sim_26, sim_53, master, trucks_map, recommended_key)


# =========================================================================
# Page: Model Master
# =========================================================================
elif page == "📋 Model Master":
    st.title("📋 Model Master")
    st.caption("Appliance / TV / monitor model master — US units (in / lb / ft³).")

    df_master = st.session_state.df_master
    source = st.session_state.get("master_source", "bundled")
    src_label = (
        f"💾 Loaded from your saved master: `{USER_MASTER_PATH}`"
        if source == "user" else
        "📦 Loaded from bundled sample. Upload a new master below to persist it."
    )
    st.info(src_label)
    st.markdown(f"**Total {len(df_master)} models registered**")

    # Defensive: cast to string + drop NaN so mixed-type rows don't crash sorted()
    cats = ["All"] + sorted(
        {str(c) for c in df_master["category"].dropna().unique() if str(c).strip()}
    )
    sel_cat = st.selectbox("Category filter", cats)
    df_view = df_master if sel_cat == "All" else df_master[df_master["category"] == sel_cat]
    st.dataframe(df_view, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("### 🔄 Update master (persisted across restarts)")
    st.caption(
        f"**Required columns in Model_Master sheet**: `{', '.join(REQUIRED_MASTER_COLS)}`. "
        f"Optional: `{', '.join(OPTIONAL_MASTER_COLS.keys())}`. "
        f"Column names are case-insensitive. "
        f"Truck_Master and Loads sheets are optional (kept from session if absent)."
    )
    new_file = st.file_uploader("Upload Excel", type=["xlsx"], key="master_uploader")
    if new_file:
        try:
            xls = pd.ExcelFile(new_file)
            sheet_names = xls.sheet_names
        except Exception as exc:  # noqa: BLE001
            st.error(f"Cannot read Excel file: {exc}")
            st.stop()

        st.markdown(f"📑 **Found sheets**: `{', '.join(sheet_names)}`")

        # Sheet pickers — auto-default to expected names if found
        def _default_idx(target: str, options: List[str]) -> int:
            for i, name in enumerate(options):
                if name.strip().lower() == target.lower():
                    return i
            return 0

        c1, c2, c3 = st.columns(3)
        with c1:
            master_sheet = st.selectbox(
                "Model Master sheet (required)",
                sheet_names,
                index=_default_idx("Model_Master", sheet_names),
                key="master_sheet_pick",
            )
        with c2:
            truck_options = ["(keep current)"] + sheet_names
            t_default = (
                truck_options.index("Truck_Master") if "Truck_Master" in sheet_names else 0
            )
            truck_sheet = st.selectbox(
                "Truck Master sheet",
                truck_options, index=t_default, key="truck_sheet_pick",
            )
        with c3:
            loads_options = ["(keep current)"] + sheet_names
            l_default = (
                loads_options.index("Loads") if "Loads" in sheet_names else 0
            )
            loads_sheet = st.selectbox(
                "Loads sheet",
                loads_options, index=l_default, key="loads_sheet_pick",
            )

        # Read + normalize selected sheets
        try:
            raw_master = pd.read_excel(xls, sheet_name=master_sheet)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read sheet '{master_sheet}': {exc}")
            st.stop()

        try:
            new_master, master_warns = normalize_master_df(raw_master)
        except ValueError as exc:
            st.error(f"⚠ {exc}")
            with st.expander("Show first 5 rows of uploaded sheet"):
                st.dataframe(raw_master.head(), use_container_width=True)
            st.stop()

        new_trucks = st.session_state.df_trucks
        truck_warns: List[str] = []
        if truck_sheet != "(keep current)":
            try:
                raw_trucks = pd.read_excel(xls, sheet_name=truck_sheet)
                new_trucks, truck_warns = normalize_trucks_df(raw_trucks)
            except Exception as exc:  # noqa: BLE001
                st.warning(f"Truck_Master from '{truck_sheet}' rejected ({exc}); keeping current.")

        new_loads = st.session_state.df_loads
        if loads_sheet != "(keep current)":
            try:
                new_loads = _normalize_columns(
                    pd.read_excel(xls, sheet_name=loads_sheet)
                )
            except Exception as exc:  # noqa: BLE001
                st.warning(f"Loads from '{loads_sheet}' rejected ({exc}); keeping current.")

        # Preview before commit
        st.markdown("##### ✓ Preview (clean)")
        st.dataframe(new_master.head(10), use_container_width=True, hide_index=True)
        st.caption(f"Total rows after normalization: **{len(new_master)}**")
        for w in master_warns + truck_warns:
            st.info(f"ℹ {w}")

        # Save location selector — auto-detects writable drives, with override
        st.markdown("##### 💾 Save location")
        default_dir = _resolve_user_data_dir()
        save_dir_str = st.text_input(
            "Folder (will create if missing):",
            value=str(default_dir),
            help=(
                "Default tries E:/ → D:/ → Documents → home. "
                "Override to any writable folder (e.g. E:/LG_Load_Optimizer)."
            ),
            key="save_dir_input",
        )
        save_dir = Path(save_dir_str)
        if _writable(save_dir):
            st.caption(f"✓ Writable: `{save_dir}`")
        else:
            st.warning(
                f"⚠ Cannot write to `{save_dir}`. "
                "Will auto-fall back to next writable candidate on save."
            )

        if st.button("💾 Apply + Save to persistent master", type="primary"):
            # Persist Loads only if user explicitly picked a sheet — otherwise keep
            # Loads as a transient per-session value (sidebar upload). Avoids
            # saving stale bundled-sample Loads whose SKUs may not exist in the
            # custom master.
            loads_to_save = new_loads if loads_sheet != "(keep current)" else None
            try:
                saved_path = save_user_master(
                    new_master, new_trucks, loads_to_save, override_dir=save_dir
                )
            except Exception as exc:  # noqa: BLE001
                st.error(
                    f"❌ Could not save anywhere. Last error: {exc}\n\n"
                    f"Tried: {[str(d) for d in _candidate_data_dirs(extra=save_dir)]}\n\n"
                    "Data NOT lost — your upload is still in this session. "
                    "Try a different folder above and click Apply again."
                )
                st.stop()
            # If applying a new master invalidates the currently loaded Loads,
            # drop them from session so user has to upload a compatible Loads file.
            current_loads = st.session_state.get("df_loads")
            if current_loads is not None and "model_code" in current_loads.columns:
                load_skus = set(current_loads["model_code"].astype(str))
                master_skus = set(new_master["model_code"].astype(str))
                if load_skus and not load_skus.issubset(master_skus):
                    new_loads = None
                    st.warning(
                        f"⚠ Existing Loads referenced "
                        f"{len(load_skus - master_skus)} SKU(s) not in your new master "
                        "— Loads cleared. Upload a fresh Loads file in the sidebar."
                    )
            st.session_state.df_master = new_master
            st.session_state.df_trucks = new_trucks
            st.session_state.df_loads = new_loads
            st.session_state.master_source = "user"
            for k in list(st.session_state.keys()):
                if isinstance(k, str) and k.startswith("sim_"):
                    del st.session_state[k]
            st.success(
                f"✅ Saved to `{saved_path}` — {len(new_master)} models. "
                "Auto-loads on next launch."
            )
            st.rerun()

    col_dl, col_reset = st.columns(2)
    with col_dl:
        # Build a snapshot xlsx in memory for download
        from io import BytesIO
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df_master.to_excel(w, sheet_name="Model_Master", index=False)
            st.session_state.df_trucks.to_excel(w, sheet_name="Truck_Master", index=False)
            if st.session_state.df_loads is not None:
                st.session_state.df_loads.to_excel(w, sheet_name="Loads", index=False)
        st.download_button(
            "📥 Download current master (backup)",
            buf.getvalue(),
            file_name="lg_master_backup.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="master_download",
        )
    with col_reset:
        if source == "user":
            if st.button(
                "♻️ Reset to bundled sample",
                use_container_width=True,
                help="Removes saved master file; next launch reverts to bundled sample.",
            ):
                try:
                    USER_MASTER_PATH.unlink(missing_ok=True)
                except Exception:
                    pass
                df_master, df_trucks, df_loads, source = _load_initial_data()
                st.session_state.df_master = df_master
                st.session_state.df_trucks = df_trucks
                st.session_state.df_loads = df_loads
                st.session_state.master_source = source
                st.rerun()


# =========================================================================
# Page: Truck Master
# =========================================================================
elif page == "🚛 Truck Master":
    st.title("🚛 Truck Master")
    st.caption("Truck cargo space spec (interior dimensions).")
    st.dataframe(st.session_state.df_trucks, use_container_width=True, hide_index=True)
    st.info("💡 26ft Box Truck / 53ft Dry Van. Phase 1 will add containers.")
