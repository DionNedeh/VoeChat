from __future__ import annotations

import os
import sys
from pathlib import Path

from vlite.api import VLiteAPI


def main() -> int:
    try:
        import webview
    except Exception as exc:
        print("pywebview is required to run VLite. Install requirements.txt first.")
        print(exc)
        return 1

    root = Path(__file__).resolve().parent
    ui_path = root / "frontend" / "index.html"
    api = VLiteAPI(app_root=root)

    window = webview.create_window(
        "VLite",
        str(ui_path),
        js_api=api,
        width=1220,
        height=790,
        min_size=(860, 620),
        background_color="#080a0f",
    )
    api.set_window(window)
    webview.start(debug="--debug" in sys.argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
