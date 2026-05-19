# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for LG Load Optimizer (Streamlit app).
Build:  pyinstaller app.spec --clean --noconfirm
Output: dist/LG_Load_Optimizer  (single-file binary)
"""
from PyInstaller.utils.hooks import collect_all, copy_metadata

datas, binaries, hiddenimports = [], [], []

# Streamlit + frontend stack need their assets and metadata.
# pulp bundles the CBC MILP solver binary under pulp/solverdir/cbc/<os>/<arch>/cbc —
# collect_all pulls that into the bundle so the .exe ships with CBC ready to run.
for pkg in ("streamlit", "altair", "plotly", "narwhals", "pulp"):
    p_datas, p_binaries, p_hidden = collect_all(pkg)
    datas += p_datas
    binaries += p_binaries
    hiddenimports += p_hidden

# Streamlit introspects installed packages — give it metadata
for pkg in (
    "streamlit", "plotly", "pandas", "numpy", "altair", "openpyxl",
    "Pillow", "pyarrow", "narwhals", "jinja2", "pulp",
):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

# Project data files — pyinstaller copies these into the bundle root
datas += [
    ("app.py", "."),
    ("engine", "engine"),
    ("data", "data"),
]

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        "streamlit",
        "streamlit.web.cli",
        "streamlit.web.bootstrap",
        "streamlit.web.server",
        "streamlit.runtime",
        "streamlit.runtime.scriptrunner.magic_funcs",
        "streamlit.runtime.scriptrunner.script_runner",
        "streamlit.runtime.fragment",
        "streamlit.runtime.caching",
        "streamlit.runtime.caching.cache_data_api",
        "streamlit.runtime.caching.cache_resource_api",
        "streamlit.elements",
        "streamlit.delta_generator",
        "engine",
        "engine.best_packer",
        "engine.milp_solver",
        "engine.sa_refiner",
        "engine.router",
        "engine.domain_rules",
        "engine.explain",
        "engine.zone_aggregator",
        "engine.demote_layer",
        "engine.categorizer",
        "engine.email_ui",
        "engine.email_sender",
        "engine.excel_calculator",
        "engine.i18n",
        "pulp",
        "pulp.apis",
        "pulp.apis.coin_api",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib"],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LG_Load_Optimizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep console so users can see errors during early rollout
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
