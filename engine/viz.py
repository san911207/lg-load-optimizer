"""
Plotly 3D 시각화 - 트럭 적재 결과를 인터랙티브 HTML로 출력
"""
import plotly.graph_objects as go
from engine.packer import PackResult

# 카테고리별 색상 (가전·TV·모니터 구분)
CATEGORY_COLORS = {
    "Refrigerator": "#2E86AB",      # 진한 파랑
    "Washer": "#A23B72",            # 진한 자주
    "Dryer": "#C73E7E",
    "Dishwasher": "#7B2CBF",
    "Range": "#F18F01",             # 주황
    "WallOven": "#E76F51",
    "Microwave": "#F4A261",
    "TV": "#06A77D",                # 진한 녹색
    "Monitor": "#80B918",           # 라임
    "_default": "#808080",
}

def _color_for(category: str) -> str:
    for key, color in CATEGORY_COLORS.items():
        if key in category:
            return color
    return CATEGORY_COLORS["_default"]


def _cuboid_mesh(x, y, z, dx, dy, dz, color, name, hovertext):
    """직육면체를 Plotly Mesh3d로 표현"""
    # 8 vertices
    xs = [x,    x+dx, x+dx, x,    x,    x+dx, x+dx, x   ]
    ys = [y,    y,    y+dy, y+dy, y,    y,    y+dy, y+dy]
    zs = [z,    z,    z,    z,    z+dz, z+dz, z+dz, z+dz]
    # 12 triangles (6 faces × 2)
    i = [0,0, 4,4, 0,0, 1,1, 2,2, 3,3]
    j = [1,2, 5,6, 1,5, 2,6, 3,7, 0,4]
    k = [2,3, 6,7, 5,4, 6,5, 7,6, 4,7]
    return go.Mesh3d(
        x=xs, y=ys, z=zs, i=i, j=j, k=k,
        color=color, opacity=0.85,
        flatshading=True,
        name=name,
        hovertext=hovertext, hoverinfo="text",
        showlegend=False,
    )


def build_3d_figure(res: PackResult) -> go.Figure:
    """PackResult를 받아 Plotly Figure 반환"""
    fig = go.Figure()

    # 1) 트럭 외곽 (와이어프레임)
    L, W, H = res.truck_length_mm, res.truck_width_mm, res.truck_height_mm
    edges = [
        # 바닥 사각형
        [(0,0,0),(L,0,0)],[(L,0,0),(L,W,0)],[(L,W,0),(0,W,0)],[(0,W,0),(0,0,0)],
        # 천장 사각형
        [(0,0,H),(L,0,H)],[(L,0,H),(L,W,H)],[(L,W,H),(0,W,H)],[(0,W,H),(0,0,H)],
        # 수직 기둥
        [(0,0,0),(0,0,H)],[(L,0,0),(L,0,H)],[(L,W,0),(L,W,H)],[(0,W,0),(0,W,H)],
    ]
    for (p1,p2) in edges:
        fig.add_trace(go.Scatter3d(
            x=[p1[0],p2[0]], y=[p1[1],p2[1]], z=[p1[2],p2[2]],
            mode="lines", line=dict(color="black", width=3),
            showlegend=False, hoverinfo="skip",
        ))

    # 2) 적재된 박스들
    cat_seen = set()
    for it in res.fitted_items:
        color = _color_for(it.category)
        hover = (f"<b>{it.model_code}</b><br>"
                 f"Seq #{it.seq} / Zone {it.zone}<br>"
                 f"Pos: ({it.pos_x:.0f}, {it.pos_y:.0f}, {it.pos_z:.0f}) mm<br>"
                 f"Dim: {it.dim_x:.0f}×{it.dim_y:.0f}×{it.dim_z:.0f} mm<br>"
                 f"Weight: {it.weight_kg:.1f} kg / Rot: {it.rotation}")
        fig.add_trace(_cuboid_mesh(
            it.pos_x, it.pos_y, it.pos_z,
            it.dim_x, it.dim_y, it.dim_z,
            color, it.model_code, hover,
        ))
        # 범례용 더미 (카테고리별 1개만)
        if it.category not in cat_seen:
            cat_seen.add(it.category)
            fig.add_trace(go.Scatter3d(
                x=[None], y=[None], z=[None],
                mode="markers", marker=dict(size=10, color=color),
                name=it.category, showlegend=True,
            ))

    # 3) 레이아웃
    fig.update_layout(
        title=dict(
            text=f"<b>Load {res.load_id}</b> | {res.truck_display}<br>"
                 f"<sub>Volume {res.volume_util_pct}% | Weight {res.weight_util_pct}% | "
                 f"Fitted {res.fitted_count} | Unfitted {res.unfitted_count}</sub>",
            x=0.5, xanchor="center",
        ),
        scene=dict(
            xaxis_title="Length (mm) — 트럭 전후",
            yaxis_title="Width (mm) — 좌우",
            zaxis_title="Height (mm) — 상하",
            aspectmode="data",
            camera=dict(eye=dict(x=1.6, y=1.6, z=1.0)),
        ),
        margin=dict(l=0, r=0, t=80, b=0),
        height=700,
        legend=dict(orientation="h", y=-0.05),
    )
    return fig
