#!/usr/bin/env python
"""vouch dashboard: local web UI to watch, configure, and control runs.

    /Users/renzof/colibri/laya-venv/bin/python run_ui.py            # 127.0.0.1:8787
    /Users/renzof/colibri/laya-venv/bin/python run_ui.py --port 8080

Binds 127.0.0.1 by default on purpose: this API can start processes and
read run logs; do not expose it to your network. Real-money runs stay
blocked unless VOUCH_UI_ALLOW_REAL=1 is set in the SERVER environment.

Optional: `pip install websockets` inside the venv enables the live
trade feed; everything else is already a dependency (fastapi, uvicorn).
"""

import argparse

import uvicorn

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="vouch dashboard (local web UI)")
    ap.add_argument("--host", default="127.0.0.1", help="bind address; 127.0.0.1 on purpose")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    uvicorn.run("server.app:app", host=a.host, port=a.port, log_level="warning")
