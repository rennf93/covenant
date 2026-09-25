"""vouch dashboard API: see what's running, configure it, start/stop/restart.

Local-only by design: binds 127.0.0.1 unless you explicitly pass --host.
Every runner (sim/shadow/backtest/real) is spawned as a subprocess with
the settings from out/ui/config.json injected via VOUCH_RULES / VOUCH_FEE_BPS /
VOUCH_VENUE, so the UI never edits strategy code or env files directly.

REAL MODE IS GATED: the API refuses to start a real-money run unless
VOUCH_UI_ALLOW_REAL=1 is set in the SERVER's environment AND the request
carries confirm_real=true AND the chosen venue is configured. A web UI
click is not a human running run_real.py by hand; treat this as a
convenience for someone who has already read README.md.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vouch.execution import available_venues
from vouch.rules import RAILS, Rules, validate

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
CONFIG_PATH = OUT / "ui" / "config.json"
RUNS_LOG = OUT / "ui" / "runs"

DEFAULT_CONFIG = {
    "rules": Rules().to_dict(),
    "fee_bps": 60,
    "venue": "paper",
    "product": "SOL-USD",
    "s1": {
        "provider": "local",        # local | server | openrouter
        "url": "http://127.0.0.1:9989",
        "model": "",                # openrouter model id
        "api_key": "",              # server bearer key OR openrouter key
    },
    "s2": {
        "base_url": "",             # e.g. http://127.0.0.1:9998/v1 or openrouter.ai/api/v1
        "model": "",
        "api_key": "",
    },
    "defaults": {
        "sim": {"ticks": 300, "epoch": 50, "seed": 42, "cash": 1000.0},
        "shadow": {"minutes": 480, "interval": 15, "epoch_minutes": 15, "cash": 10.0, "websocket": True},
        "backtest": {"minutes": 1440, "epoch_len": 60, "cash": 1000.0, "s2": True},
        "real": {"minutes": 480, "interval": 20, "epoch_minutes": 60, "max_usd": 10.0},
    },
}

S1_PROVIDERS = ("local", "server", "openrouter")


def _load_config() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except Exception:  # noqa: BLE001 - missing/corrupt config: defaults are safe
        cfg = {}
    merged = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    for k, v in cfg.items():
        if k == "rules" and isinstance(v, dict):
            merged["rules"] |= {kk: v[kk] for kk in v if kk in merged["rules"]}
        elif k in ("s1", "s2") and isinstance(v, dict):
            merged[k] |= {kk: v[kk] for kk in v if kk in merged[k]}
        elif k == "defaults" and isinstance(v, dict):
            for mode, d in v.items():
                if mode in merged["defaults"] and isinstance(d, dict):
                    merged["defaults"][mode] |= d
        else:
            merged[k] = v
    return merged


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


app = FastAPI(title="vouch dashboard")


# --- process manager --------------------------------------------------------

class Run:
    def __init__(self, run_id: str, mode: str, spec: dict, cmd: list[str], env: dict):
        self.id = run_id
        self.mode = mode
        self.spec = spec
        self.cmd = cmd
        self.env = env
        self.console = RUNS_LOG / f"{run_id}.log"
        self.started_at = time.time()
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        self.console.parent.mkdir(parents=True, exist_ok=True)
        with self.console.open("ab") as logf:
            logf.write(f"\n=== start {datetime.now(timezone.utc).isoformat()}: {' '.join(self.cmd)}\n".encode())
            logf.flush()
            self.proc = subprocess.Popen(
                self.cmd, cwd=ROOT, env=self.env,
                stdout=logf, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )

    def stop(self) -> bool:
        if not self.proc or self.proc.poll() is not None:
            return False
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return False
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(timeout=5)
        return True

    def state(self) -> dict:
        running = self.proc is not None and self.proc.poll() is None
        return {
            "id": self.id, "mode": self.mode, "spec": self.spec,
            "pid": self.proc.pid if self.proc else None,
            "running": running,
            "exit_code": None if running else (self.proc.returncode if self.proc else None),
            "started_at": datetime.fromtimestamp(self.started_at, tz=timezone.utc).isoformat(),
            "uptime_s": round(time.time() - self.started_at, 1),
            "console_tail": _tail_file(self.console, 30),
        }


RUNS: dict[str, Run] = {}


def _tail_file(path: Path, n: int) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()[-n:]
    except Exception:  # noqa: BLE001
        return []


# --- models -----------------------------------------------------------------

class RunRequest(BaseModel):
    mode: str
    params: dict = {}
    confirm_real: bool = False


class ConfigRequest(BaseModel):
    config: dict


# --- command builders -------------------------------------------------------

def _build_run(mode: str, params: dict, cfg: dict) -> tuple[str, list[str], dict, dict]:
    run_id = f"{mode}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    py = sys.executable
    env = os.environ.copy()
    rules = dict(cfg["rules"])
    env["VOUCH_RULES"] = json.dumps(rules)
    env["VOUCH_FEE_BPS"] = str(params.get("fee_bps", cfg["fee_bps"]))
    env["VOUCH_VENUE"] = str(params.get("venue", cfg["venue"]))
    if env["VOUCH_VENUE"] == "coinbase":
        env.setdefault("VOUCH_CB_PRODUCT", str(params.get("product", cfg["product"])))

    # System-1 provider (local laya | laya server | openrouter) and the
    # System-2 rewriter endpoint, straight from dashboard settings.
    s1 = cfg["s1"]
    env["VOUCH_S1_PROVIDER"] = s1["provider"]
    env["VOUCH_S1_URL"] = s1["url"]
    env["VOUCH_S1_API_KEY"] = s1["api_key"]
    env["VOUCH_S1_MODEL"] = s1["model"]
    s2 = cfg["s2"]
    if s2["base_url"]:
        env["VOUCH_S2_BASE_URL"] = s2["base_url"]
        env["VOUCH_S2_MODEL"] = s2["model"]
        env["VOUCH_S2_KEY"] = s2["api_key"]

    d = cfg["defaults"].get(mode, {})
    if mode == "sim":
        cmd = [py, "run.py", "--ticks", str(params.get("ticks", d["ticks"])),
               "--epoch", str(params.get("epoch", d["epoch"])),
               "--seed", str(params.get("seed", d["seed"])),
               "--cash", str(params.get("cash", d["cash"])),
               "--out", f"out/{run_id}"]
    elif mode == "shadow":
        cmd = [py, "run_shadow.py",
               "--minutes", str(params.get("minutes", d["minutes"])),
               "--interval", str(params.get("interval", d["interval"])),
               "--epoch-minutes", str(params.get("epoch_minutes", d["epoch_minutes"])),
               "--cash", str(params.get("cash", d["cash"])),
               "--seed", run_id]
        if not params.get("websocket", d.get("websocket", True)):
            cmd.append("--no-websocket")
        # Attestation: PROVEN_* vars set on the SERVER process already reach
        # the spawned run via env = os.environ.copy(); passing --attest here
        # additionally lets a dashboard request (params.attest=true) switch a
        # shadow run's receipts on. Ledger-only unless the full PROVEN_RPC_URL
        # / PROVEN_PRIVATE_KEY / PROVEN_CONTRACT_ADDRESS trio is also set.
        if params.get("attest"):
            cmd.append("--attest")
    elif mode == "backtest":
        cmd = [py, "run_backtest.py",
               "--minutes", str(params.get("minutes", d["minutes"])),
               "--epoch-len", str(params.get("epoch_len", d["epoch_len"])),
               "--cash", str(params.get("cash", d["cash"])),
               "--tag", run_id]
        if not params.get("s2", d.get("s2", True)):
            cmd.append("--no-s2")
        if params.get("fee_bps") is not None:
            cmd += ["--fee-bps", str(params["fee_bps"])]
    elif mode == "real":
        cmd = [py, "run_real.py",
               "--minutes", str(params.get("minutes", d["minutes"])),
               "--interval", str(params.get("interval", d["interval"])),
               "--epoch-minutes", str(params.get("epoch_minutes", d["epoch_minutes"])),
               "--max-usd", str(params.get("max_usd", d["max_usd"])),
               "--seed", run_id, "--confirm-real"]
    else:
        raise ValueError(f"unknown mode {mode}")
    return run_id, cmd, env, {"mode": mode, "params": params}


# --- API --------------------------------------------------------------------

@app.get("/api/overview")
def overview():
    cfg = _load_config()
    active = [r.state() for r in RUNS.values()]
    sessions = []
    if OUT.exists():
        for p in sorted(OUT.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.is_dir() or p.name in ("ui",):
                continue
            sessions.append({
                "id": p.name,
                "files": sorted(f.name for f in p.iterdir() if f.is_file()),
                "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
                "summary": _read_json(p / "summary.json"),
                "is_active_run": any(s["id"].endswith(p.name) for s in active) or p.name in RUNS,
            })
    return {"config": cfg, "venues": available_venues(), "rails": {k: list(v) for k, v in RAILS.items()},
            "active": active, "sessions": sessions[:40],
            "real_mode_allowed": os.environ.get("VOUCH_UI_ALLOW_REAL") == "1"}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None


@app.get("/api/config")
def get_config():
    return _load_config()


@app.put("/api/config")
def put_config(req: ConfigRequest):
    cfg = _load_config()
    incoming = req.config
    if "rules" in incoming:
        ok, msg = validate(incoming["rules"])
        if not ok:
            raise HTTPException(400, f"rules violate rails: {msg}")
        cfg["rules"] |= {k: incoming["rules"][k] for k in incoming["rules"] if k in cfg["rules"]}
    for k in ("fee_bps", "venue", "product"):
        if k in incoming:
            cfg[k] = incoming[k]
    if "s1" in incoming and isinstance(incoming["s1"], dict):
        if "provider" in incoming["s1"] and incoming["s1"]["provider"] not in S1_PROVIDERS:
            raise HTTPException(400, f"s1.provider must be one of {S1_PROVIDERS}")
        cfg["s1"] |= {k: incoming["s1"][k] for k in incoming["s1"] if k in cfg["s1"]}
    if "s2" in incoming and isinstance(incoming["s2"], dict):
        cfg["s2"] |= {k: incoming["s2"][k] for k in incoming["s2"] if k in cfg["s2"]}
    if "defaults" in incoming and isinstance(incoming["defaults"], dict):
        for mode, d in incoming["defaults"].items():
            if mode in cfg["defaults"] and isinstance(d, dict):
                cfg["defaults"][mode] |= d
    _save_config(cfg)
    return cfg


@app.post("/api/runs")
def start_run(req: RunRequest):
    cfg = _load_config()
    params = dict(req.params)
    if req.mode == "real":
        if os.environ.get("VOUCH_UI_ALLOW_REAL") != "1":
            raise HTTPException(403, "Real mode is blocked from the UI. Start run_real.py by hand, "
                                     "or set VOUCH_UI_ALLOW_REAL=1 in the SERVER environment if you accept that.")
        if not req.confirm_real:
            raise HTTPException(403, "real mode requires confirm_real=true")
        venue = str(params.get("venue", cfg["venue"]))
        if venue != "coinbase":
            raise HTTPException(400, "real mode needs venue=coinbase")
        v = next(v for v in available_venues() if v["id"] == "coinbase")
        if not v["configured"]:
            raise HTTPException(400, f"coinbase venue not configured; missing env: {v['missing_env']}")
    try:
        run_id, cmd, env, spec = _build_run(req.mode, params, cfg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    run = Run(run_id, req.mode, spec, cmd, env)
    run.start()
    RUNS[run_id] = run
    return run.state()


@app.get("/api/runs/{run_id}")
def run_state(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(404, "unknown run")
    return RUNS[run_id].state()


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(404, "unknown run")
    stopped = RUNS[run_id].stop()
    return {"id": run_id, "stopped": stopped}


@app.post("/api/runs/{run_id}/restart")
def restart_run(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(404, "unknown run")
    old = RUNS[run_id]
    old.stop()
    run = Run(old.id, old.mode, old.spec, old.cmd, old.env)
    run.start()
    RUNS[run_id] = run
    return run.state()


@app.get("/api/sessions/{name}")
def session_files(name: str, limit: int = 60):
    if "/" in name or ".." in name or name == "ui":
        raise HTTPException(400, "bad session name")
    p = OUT / name
    if not p.is_dir():
        raise HTTPException(404, "no such session")

    def tail_jsonl(fname: str) -> list[dict]:
        rows = []
        f = p / fname
        if f.exists():
            lines = f.read_text(errors="replace").splitlines()[-limit:]
            for line in lines:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    return {
        "id": name,
        "summary": _read_json(p / "summary.json"),
        "decisions": tail_jsonl("decisions.jsonl"),
        "trades": tail_jsonl("trades.jsonl"),
        "s2": tail_jsonl("s2-history.jsonl"),
        "console": _tail_file(RUNS_LOG / f"{name}.log", 60) if (RUNS_LOG / f"{name}.log").exists() else [],
    }


@app.get("/api/s1/health")
def s1_health():
    """Probe the configured System-1 provider. For the server provider this
    tells you if the laya server is up (and which adapters are loaded); a
    cold server answers health fast but the first predict may still be slow."""
    cfg = _load_config()
    if cfg["s1"]["provider"] != "server":
        return {"provider": cfg["s1"]["provider"], "probe": "n/a (not a server provider)"}
    from vouch.s1_backends import LayaServerBackend
    b = LayaServerBackend(cfg["s1"]["url"], api_key=cfg["s1"]["api_key"])
    h = b.health()
    return {"provider": "server", "url": cfg["s1"]["url"], "probe": h or "unreachable"}


# static dashboard (registered last so /api routes win)
app.mount("/", StaticFiles(directory=str(ROOT / "server" / "static"), html=True), name="ui")
