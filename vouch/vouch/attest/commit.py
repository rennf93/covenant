"""Epoch close orchestration: evidence bundle first, chain commit second.

Interlocks (hard rules, enforced here):
  1. A chain write happens ONLY when cfg.connected is true (COVENANT_ATTEST=1
     plus rpc url, private key and contract address). An enabled-but-unconnected
     config is the ledger-only demo path: receipts are recorded and evidence
     is written, but nothing touches the chain.
  2. The evidence bundle is written BEFORE the chain call. If the bridge or
     the chain fails, the evidence still exists on disk and the commit can be
     retried without rebuilding anything.
  3. Self-audit: after a successful commit of a non-empty epoch, the first
     receipt is verified onchain through the bridge. A valid=False result is
     a CommitError; an operator must never stay silent when its own receipt
     fails onchain verification.

Layering: leaf. Attest must never import vouch.engine, vouch.venues, or
vouch.server; CommitError comes from vouch.exceptions.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from vouch.attest import merkle
from vouch.attest.ledger import EpochLedger
from vouch.attest.receipts import usdg_from_usd
from vouch.config import load_settings
from vouch.exceptions import CommitError


@dataclass
class AttestationConfig:
    """Where and whether to attest; COVENANT_ATTEST=1 plus full env = chain mode."""

    enabled: bool
    rpc_url: str
    private_key: str
    contract_address: str
    chain: str
    evidence_dir: Path

    @classmethod
    def from_env(cls) -> AttestationConfig:
        a = load_settings().attest
        return cls(
            enabled=a.enabled == "1",
            rpc_url=a.rpc_url,
            private_key=a.private_key,
            contract_address=a.contract_address,
            chain=a.chain,
            evidence_dir=Path(a.evidence_dir),
        )

    @property
    def connected(self) -> bool:
        return (
            self.enabled
            and bool(self.rpc_url)
            and bool(self.private_key)
            and bool(self.contract_address)
        )


class Attestor:
    """Owns the open epoch ledgers for one run and performs epoch commits."""

    def __init__(
        self, cfg: AttestationConfig, run_dir: Path, strategy_id: int, venue: int, instrument: str
    ) -> None:
        self.cfg = cfg
        self.run_dir = Path(run_dir)
        self.strategy_id = strategy_id
        self.venue = venue
        self.instrument = instrument
        self._ledgers: dict[int, EpochLedger] = {}

    def start_epoch(self, epoch_index: int) -> EpochLedger:
        """Create or reattach the ledger for epoch_index."""
        ledger = EpochLedger(
            self.run_dir, self.strategy_id, epoch_index, self.venue, self.instrument
        )
        self._ledgers[epoch_index] = ledger
        return ledger

    def commit_epoch(
        self,
        ledger: EpochLedger,
        *,
        equity_usd: int | float,
        net_flow_usd: int | float = 0,
        strategy_name: str = "",
    ) -> dict:
        """Close an epoch: write the evidence bundle, then (only if connected)
        commit onchain and self-audit.

        equity_usd and net_flow_usd are human USD amounts; both are converted
        to USDG base units (6 decimals) before the call.
        """
        equity_usdg = usdg_from_usd(equity_usd)
        net_flow_usdg = usdg_from_usd(net_flow_usd)

        hashes = ledger.receipt_hashes()
        if hashes:
            tree = merkle.build_merkle_tree(hashes)
            trades_root = tree.root
        else:
            tree = None
            trades_root = merkle.ZERO32

        bundle_path = self._evidence_path(ledger)
        bundle = {
            "protocol": "covenant-v1",
            "strategyId": self.strategy_id,
            "strategyName": strategy_name,
            "epochIndex": ledger.epoch_index,
            "committedAt": int(time.time()),
            "equityUsdg": equity_usdg,
            "netFlowUsdg": net_flow_usdg,
            "tradesRoot": trades_root,
            "venue": self.venue,
            "instrument": self.instrument,
            "receipts": [
                {
                    "receipt": r.to_dict(),
                    "hash": h,
                    "proof": tree.proof(i) if tree is not None else [],
                    "valid": True,
                }
                for i, (r, h) in enumerate(zip(ledger.receipts, hashes, strict=False))
            ],
        }
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        evidence_uri = self._evidence_uri(bundle_path)

        base = {
            "committed": False,
            "evidenceUri": evidence_uri,
            "tradesRoot": trades_root,
            "receipts": len(ledger),
        }
        if not self.cfg.connected:
            return {**base, "reason": "not connected (ledger-only mode)"}

        result = self._call_bridge(
            {
                "action": "commit",
                "strategyId": str(self.strategy_id),
                "epochIndex": str(ledger.epoch_index),
                "equity": str(equity_usdg),
                "netFlow": str(net_flow_usdg),
                "tradesRoot": trades_root,
                "evidenceUri": evidence_uri,
            }
        )
        out = {**base, "committed": True, "hash": result["hash"]}
        if "blockNumber" in result:
            out["blockNumber"] = result["blockNumber"]

        if tree is not None:
            first_hash = hashes[0]
            audit = self._call_bridge(
                {
                    "action": "verify",
                    "strategyId": str(self.strategy_id),
                    "epochIndex": str(ledger.epoch_index),
                    "proof": tree.proof(0),
                    "receiptHash": first_hash,
                }
            )
            out["selfAudit"] = {"receipt": first_hash, "valid": bool(audit.get("valid"))}
            if not out["selfAudit"]["valid"]:
                raise CommitError(
                    f"self-audit failed: receipt {first_hash} did not verify onchain "
                    f"for epoch {ledger.epoch_index}"
                )
        return out

    def _evidence_path(self, ledger: EpochLedger) -> Path:
        return self.cfg.evidence_dir / f"epoch-{ledger.epoch_index}.json"

    def _evidence_uri(self, bundle_path: Path) -> str:
        resolved = bundle_path.resolve()
        cwd = Path.cwd().resolve()
        try:
            return str(resolved.relative_to(cwd))
        except ValueError:
            return str(resolved)

    def _call_bridge(self, payload: dict) -> dict:
        proc = subprocess.run(
            ["node", str(Path(__file__).resolve().parent / "bridge.mjs")],
            cwd=str(Path(__file__).resolve().parents[1]),
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )
        try:
            out: dict = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise CommitError(
                f"bridge produced unparseable output (exit {proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()[:200]}"
            ) from None
        if proc.returncode != 0 or "error" in out:
            raise CommitError(f"bridge error: {out.get('error', f'exit {proc.returncode}')}")
        return out
