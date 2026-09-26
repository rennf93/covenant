#!/usr/bin/env python
"""Serve OUR fine-tuned laya checkpoint on its own port (:9988).

    .venv/bin/python run_serve_vouch_laya.py \
        --checkpoint ~/colibri/checkpoints/laya-vouch-v1 --port 9988

Isolation contract: the stock laya server (:9989) and the stock HF cache
are never touched; this runs a separate Agent from a local training output
directory and speaks just enough of the /v1/systemone protocol for vouch's
LayaServerBackend (GET /health, POST /v1/systemone). Point vouch at it:

    VOUCH_S1_PROVIDER=server
    VOUCH_S1_URL=http://127.0.0.1:9988
    VOUCH_S1_CHECKPOINT=<the name you pass here>

The checkpoint name rides back in routing.model, which the vouch backend
checks against the pin, so a wrong-port accident is loudly visible.
"""

from __future__ import annotations

import argparse

import uvicorn
from fastapi import FastAPI, HTTPException
from laya.agent import Agent


def build_app(checkpoint: str, name: str, device: str | None):
    agent = Agent(checkpoint, device=device)
    app = FastAPI(title="laya-vouch", version="1.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "loaded": [name], "checkpoint": checkpoint}

    @app.post("/v1/systemone")
    def systemone(body: dict):
        if "state" not in body or "questions" not in body:
            raise HTTPException(400, "body needs 'state' and 'questions'")
        try:
            res = agent.predict(body["state"], body["questions"])
        except Exception as e:  # noqa: BLE001 - question/validation errors are 422s
            raise HTTPException(422, f"question rejected: {e}") from e
        return {
            "model": "laya-rl-agent",
            "answers": res["answers"],
            "usage": res.get("usage", {"input_tokens": 0, "output_tokens": 0}),
            "routing": {"model": name, "reason": "vouch fine-tuned checkpoint"},
        }

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="serve a vouch fine-tuned laya checkpoint")
    ap.add_argument("--checkpoint", required=True, help="local training output dir")
    ap.add_argument("--name", default="vouch-v1", help="checkpoint name reported in routing")
    ap.add_argument("--port", type=int, default=9988)
    ap.add_argument("--device", default=None, help="force mps/cpu (auto if omitted)")
    a = ap.parse_args()

    app = build_app(a.checkpoint, a.name, a.device)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
