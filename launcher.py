"""
PyInstaller entry point — spawns Streamlit and opens the browser.
Defensive logging so any failure is visible in the console window.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path


def _say(msg: str) -> None:
    """Immediate console print (flush so PyInstaller console shows it)."""
    print(msg, flush=True)
    sys.stdout.flush()


def _find_free_port(start: int = 8501, end: int = 8600) -> int:
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    return start


def _resource_path(rel: str) -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    else:
        base = Path(__file__).resolve().parent
    return base / rel


def _self_diagnostic() -> None:
    """Quick sanity scan — printed at startup so failures are visible.

    Issues silently broke build #25 on a colleague's PC: ModuleNotFoundError
    or CBC quarantine vanished the console before the user could read it.
    """
    _say("--- Self-diagnostic ---")
    # 1. Bundled engine modules
    expected = [
        "engine.categorizer", "engine.best_packer", "engine.router",
        "engine.milp_solver", "engine.sa_refiner", "engine.zone_aggregator",
        "engine.demote_layer",
    ]
    missing = []
    for mod in expected:
        try:
            __import__(mod)
        except Exception as e:
            missing.append(f"{mod}  →  {type(e).__name__}: {e}")
    if missing:
        _say("[!] Module import failures:")
        for m in missing:
            _say(f"    - {m}")
    else:
        _say("[OK] All engine modules import cleanly.")

    # 2. CBC solver — bundled by pulp; check it's reachable
    try:
        import pulp
        s = pulp.PULP_CBC_CMD(msg=0)
        if s.available():
            _say("[OK] CBC solver available.")
        else:
            _say("[!] CBC solver NOT available — Windows Defender may have "
                 "quarantined the bundled cbc.exe. MILP path will fall back "
                 "to heuristic; simulation should still run.")
    except Exception as e:
        _say(f"[!] PuLP/CBC check failed: {type(e).__name__}: {e}")

    # 3. Sample data
    try:
        sample = _resource_path("data/sample_input.xlsx")
        if sample.exists():
            _say(f"[OK] Sample data found: {sample}")
        else:
            _say(f"[!] Sample data MISSING at {sample} — upload a master to use the app.")
    except Exception as e:
        _say(f"[!] Sample-data check failed: {e}")

    _say("---")
    _say("")


def main() -> None:
    _say("=" * 64)
    _say("  LG Load Optimizer — starting up...")
    _say("=" * 64)
    _say("")
    _say("Please wait 10-30 seconds for Streamlit to initialize.")
    _say("A browser tab will open automatically. If not, copy the URL below.")
    _say("")
    try:
        _self_diagnostic()
    except Exception:
        _say(f"(Self-diagnostic crashed — continuing anyway:\n{traceback.format_exc()})")

    try:
        port = _find_free_port()
        app_path = _resource_path("app.py")

        if not app_path.exists():
            _say(f"ERROR: app.py not found at {app_path}")
            _say("Files in bundle:")
            for p in _resource_path(".").iterdir():
                _say(f"  - {p.name}")
            input("Press Enter to exit...")
            sys.exit(1)

        os.chdir(str(_resource_path(".")))

        url = f"http://localhost:{port}"
        _say(f"  URL: {url}")
        _say("  (Ctrl+C in this window to stop the app)")
        _say("=" * 64)
        _say("")

        # Open browser after Streamlit binds the port
        def _open() -> None:
            for _ in range(20):  # poll up to 20s
                time.sleep(1.0)
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.settimeout(0.5)
                        if s.connect_ex(("127.0.0.1", port)) == 0:
                            try:
                                webbrowser.open(url)
                            except Exception as e:
                                _say(f"(Auto-open failed: {e}. Open the URL manually.)")
                            return
                except Exception:
                    continue
            _say(
                "(Streamlit didn't bind within 20s. "
                "Check above for errors and paste the URL into a browser.)"
            )

        threading.Thread(target=_open, daemon=True).start()

        sys.argv = [
            "streamlit", "run", str(app_path),
            "--server.port", str(port),
            "--server.address", "127.0.0.1",
            "--server.headless", "true",
            "--server.runOnSave", "false",
            "--browser.gatherUsageStats", "false",
            "--global.developmentMode=false",
        ]

        from streamlit.web import cli as stcli  # noqa: WPS433  (intentional late import)
        stcli.main()

    except SystemExit:
        # streamlit cli calls sys.exit on shutdown — pass through cleanly
        raise
    except Exception:
        _say("=" * 64)
        _say("FATAL ERROR while launching Streamlit:")
        _say("")
        _say(traceback.format_exc())
        _say("=" * 64)
        input("Press Enter to close this window...")
        sys.exit(1)


if __name__ == "__main__":
    main()
