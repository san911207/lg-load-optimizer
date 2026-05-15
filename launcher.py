"""
PyInstaller entry point — spawns Streamlit on a free port and opens the
browser. Bundled .exe / .app data files are unpacked into sys._MEIPASS.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _find_free_port(start: int = 8501, end: int = 8600) -> int:
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def _resource_path(rel: str) -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    else:
        base = Path(__file__).resolve().parent
    return base / rel


def main() -> None:
    port = _find_free_port()
    app_path = _resource_path("app.py")

    # Streamlit reads relative paths from cwd → chdir to bundle root
    os.chdir(str(_resource_path(".")))

    # Open browser after Streamlit warms up
    def _open() -> None:
        time.sleep(3.0)
        webbrowser.open(f"http://localhost:{port}")

    threading.Thread(target=_open, daemon=True).start()

    sys.argv = [
        "streamlit", "run", str(app_path),
        "--server.port", str(port),
        "--server.headless", "true",
        "--server.runOnSave", "false",
        "--browser.gatherUsageStats", "false",
        "--global.developmentMode=false",
    ]
    from streamlit.web import cli as stcli
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
