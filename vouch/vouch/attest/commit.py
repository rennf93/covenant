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
     fails onchain verification. (The success is recorded in onchain.json
     first, so the retry path surfaces "already committed" and the epoch
     index can advance instead of being poisoned forever.)
  4. Continuity: failed commits never advance the local epoch index. Every
     outcome is recorded in out/<run_id>/onchain.json (attest/onchain.py) and
     the epoch stays open for retry at the next boundary.

Evidence bundle v2: bundle-level equity/netFlow are USDG base-unit integer
strings (6 decimals, "-" prefix allowed). Receipts are unchanged: they are
conformance-pinned to the SDK fixtures. Bundles are EIP-712 signed by the
operator key through the bridge (viem signTypedData, no Python crypto deps);
without a connection the signature fields carry the deterministic unsigned
fallback (zero address, 65 zero bytes) so the schema never varies. Bundles
land per run (out/<run_id>/evidence/epoch-N.json plus a stable copy under
the configured evidence root, evidence/<run_id>/epoch-N.json by default) so
runs can no longer overwrite each other. If COVENANT_IPFS_API points at a
kubo RPC the stable copy is also pinned there and the commit references
ipfs://<cid>; a publishing failure is logged, never fatal.

Layering: leaf. Attest must never import vouch.engine, vouch.venues, or
vouch.server; CommitError comes from vouch.exceptions.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from vouch.attest import merkle
from vouch.attest.ledger import EpochLedger
from vouch.attest.onchain import OnchainState, finalize_due, resync_plan
from vouch.attest.receipts import usdg_from_usd
from vouch.config import load_settings
from vouch.exceptions import CommitError
from vouch.logging import get_logger

logger = get_logger(__name__)

# Deterministic unsigned fallback: an all-zero signature can never be
# mistaken for a real one, and web-side verification rejects it explicitly.
UNSIGNED_SIGNER = "0x" + "00" * 20
UNSIGNED_SIGNATURE = "0x" + "00" * 65

# Fallback for the bundle's chainId when the bridge cannot be asked
# (ledger-only mode). Matches viem/chains; keep in sync with bridge.mjs.
CHAIN_IDS = {"arbitrum-sepolia": 421614, "arbitrum": 42161, "anvil": 31337}

PROTOCOL = "covenant-v1"
EIP712_DOMAIN_NAME = "Covenant Evidence"
EIP712_DOMAIN_VERSION = "1"

EIP712_EVIDENCE_TYPES = {
    "Evidence": [
        {"name": "protocol", "type": "string"},
        {"name": "strategyId", "type": "uint256"},
        {"name": "epochIndex", "type": "uint256"},
        {"name": "committedAt", "type": "uint64"},
        {"name": "equityUsdg", "type": "int256"},
        {"name": "netFlowUsdg", "type": "int256"},
        {"name": "tradesRoot", "type": "bytes32"},
    ]
}


def evidence_typed_data(
    *,
    chain_id: int,
    contract: str,
    strategy_id: int,
    epoch_index: int,
    committed_at: int,
    equity_base: int,
    net_flow_base: int,
    trades_root: str,
) -> dict:
    """The exact EIP-712 typed data an operator signature commits to (pure).

    domain = {name: "Covenant Evidence", version: "1", chainId, verifyingContract}.
    Message integers are decimal strings (uint256/int256 exceed JS safe
    integers); this shape is the pin the web verifier implements against.
    """
    return {
        "domain": {
            "name": EIP712_DOMAIN_NAME,
            "version": EIP712_DOMAIN_VERSION,
            "chainId": chain_id,
            "verifyingContract": contract,
        },
        "primaryType": "Evidence",
        "types": EIP712_EVIDENCE_TYPES,
        "message": {
            "protocol": PROTOCOL,
            "strategyId": str(strategy_id),
            "epochIndex": str(epoch_index),
            "committedAt": str(committed_at),
            "equityUsdg": str(equity_base),
            "netFlowUsdg": str(net_flow_base),
            "tradesRoot": trades_root,
        },
    }


def call_bridge(payload: dict) -> dict:
    """One JSON payload to bridge.mjs, one JSON result back; CommitError on any
    failure. The bridge needs node on PATH and viem resolvable from the sdk
    tree (see bridge.mjs module-resolution note)."""
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


@dataclass
class AttestationConfig:
    """Where and whether to attest; COVENANT_ATTEST=1 plus full env = chain mode."""

    enabled: bool
    rpc_url: str
    private_key: str
    contract_address: str
    chain: str
    evidence_dir: Path
    ipfs_api: str = ""

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
            ipfs_api=a.ipfs_api,
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
        self.run_id = self.run_dir.name
        self.strategy_id = strategy_id
        self.venue = venue
        self.instrument = instrument
        self._ledgers: dict[int, EpochLedger] = {}
        self.tracker = OnchainState.load(self.run_dir)
        # Frozen = a resync found a ledger/chain mismatch: chain writes are
        # refused (loudly) until a human resolves it. Evidence bundles are
        # still written; attestation must never block trading.
        self.frozen_reason = ""
        self._challenge_window: int | None = None
        self._finalized: set[int] = set(self.tracker.finalized)
        self._chain_id: int | None = None

    def freeze(self, reason: str) -> None:
        if not self.frozen_reason:
            self.frozen_reason = reason

    def start_epoch(self, epoch_index: int) -> EpochLedger:
        """Create or reattach the ledger for epoch_index."""
        ledger = EpochLedger(
            self.run_dir, self.strategy_id, epoch_index, self.venue, self.instrument
        )
        self._ledgers[epoch_index] = ledger
        return ledger

    def resync_with_chain(
        self,
        *,
        next_local_epoch: int,
        pending_receipt_epoch: int | None,
        has_local_history: bool,
    ) -> dict:
        """Startup alignment (connected mode): query the chain's epoch count
        for this strategy through the bridge and apply the resync plan.

        A "refuse" freezes the attestor: every later commit attempt raises
        CommitError with the mismatch explanation instead of writing garbage
        into a history this ledger does not own.
        """
        if not self.cfg.connected:
            return {
                "action": "skip",
                "next_epoch": next_local_epoch,
                "message": "not connected; nothing to resync",
            }
        out = self._call_bridge({"action": "getPerformance", "strategyId": str(self.strategy_id)})
        chain_count = int(out.get("epochCount", "0"))
        decision = resync_plan(
            chain_epoch_count=chain_count,
            next_local_epoch=next_local_epoch,
            has_local_history=has_local_history,
            pending_receipt_epoch=pending_receipt_epoch,
        )
        if decision.action == "refuse":
            self.freeze(decision.message)
        return {
            "action": decision.action,
            "next_epoch": decision.next_epoch,
            "message": decision.message,
        }

    def auto_finalize(self, *, now: int | None = None) -> list[int]:
        """Finalize earlier pending checkpoints whose challenge window has
        passed (permissionless; anyone may send this tx, we just do it so
        onchain PnL actually accrues). Never fatal on failure: the window
        stays open and the next boundary retries.
        """
        if not self.cfg.connected or self.frozen_reason:
            return []
        now = int(time.time()) if now is None else now
        if self._challenge_window is None:
            try:
                raw = self._call_bridge({"action": "getConfig"})
                self._challenge_window = int(raw.get("challengeWindow", "0"))
            except CommitError as e:
                logger.warning("auto-finalize skipped: cannot read challenge window: %s", e)
                return []
        pending = [
            (epoch, ts, 0)  # 0 = CheckpointStatus.Pending; ours are never challenged
            for epoch, ts in sorted(self.tracker.committed_at.items())
            if epoch not in self._finalized
        ]
        due = finalize_due(pending, now=now, window_seconds=self._challenge_window)
        done: list[int] = []
        for epoch in due:
            try:
                self._call_bridge(
                    {
                        "action": "finalize",
                        "strategyId": str(self.strategy_id),
                        "epochIndex": str(epoch),
                    }
                )
            except CommitError as e:
                logger.warning("finalize of epoch %d failed (will retry): %s", epoch, e)
                break
            self._finalized.add(epoch)
            self.tracker.record_finalized(epoch)
            done.append(epoch)
        if done:
            self.tracker.save(self.run_dir)
            logger.info("auto-finalized epochs %s", done)
        return done

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
        to USDG base units (6 decimals) before the call and into the bundle.

        Raises CommitError when the chain write fails (the epoch stays open
        and the caller retries at the next boundary) or when the attestor is
        frozen by a resync mismatch.
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

        if self.cfg.connected and self._chain_id is None:
            # Prime the chainId cache before the typed data is assembled, so
            # the EIP-712 domain is signed and stamped with the real chain.
            self._call_bridge({"action": "getPerformance", "strategyId": str(self.strategy_id)})

        committed_at = int(time.time())
        typed = evidence_typed_data(
            chain_id=self._bundle_chain_id(),
            contract=self._bundle_contract(),
            strategy_id=self.strategy_id,
            epoch_index=ledger.epoch_index,
            committed_at=committed_at,
            equity_base=equity_usdg,
            net_flow_base=net_flow_usdg,
            trades_root=trades_root,
        )
        signed = self._sign_evidence(typed) if self.cfg.connected else None

        bundle = {
            "protocol": PROTOCOL,
            "strategyId": self.strategy_id,
            "strategyName": strategy_name,
            "epochIndex": ledger.epoch_index,
            "committedAt": committed_at,
            "equityUsdg": str(equity_usdg),
            "netFlowUsdg": str(net_flow_usdg),
            "tradesRoot": trades_root,
            "venue": self.venue,
            "instrument": self.instrument,
            "signer": (signed or {}).get("signer", UNSIGNED_SIGNER),
            "chainId": int((signed or {}).get("chainId", typed["domain"]["chainId"])),
            "contract": typed["domain"]["verifyingContract"],
            "signature": (signed or {}).get("signature", UNSIGNED_SIGNATURE),
            "signedAt": committed_at,
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

        # Per-run evidence: the run copy under out/<run_id>/ for local audit,
        # the stable copy under the evidence root for the onchain URI. Runs
        # can no longer overwrite each other's bundles.
        run_copy = self.run_dir / "evidence" / f"epoch-{ledger.epoch_index}.json"
        stable_copy = self.cfg.evidence_dir / self.run_id / f"epoch-{ledger.epoch_index}.json"
        text = json.dumps(bundle, indent=2) + "\n"
        for path in (run_copy, stable_copy):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        evidence_uri = self._evidence_uri(stable_copy)
        published = False
        if self.cfg.ipfs_api:
            cid = self._ipfs_add(stable_copy)
            if cid:
                evidence_uri = f"ipfs://{cid}"
                published = True
            logger.info(
                "evidence %s %s",
                "published to IPFS" if published else "kept local",
                evidence_uri,
            )

        base = {
            "committed": False,
            "evidenceUri": evidence_uri,
            "published": published,
            "signed": signed is not None,
            "tradesRoot": trades_root,
            "receipts": len(ledger),
        }
        if not self.cfg.connected:
            return {**base, "reason": "not connected (ledger-only mode)"}

        if self.frozen_reason:
            raise CommitError(f"attestor frozen by resync: {self.frozen_reason}")
        if ledger.epoch_index <= self.tracker.last_committed_epoch:
            # Already onchain (a previous process landed it before dying, or
            # this is the retry after a self-audit failure). Report success
            # so the caller advances instead of poisoning the index.
            return {**base, "committed": True, "alreadyCommitted": True, "hash": ""}

        try:
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
        except CommitError as e:
            self.tracker.record_failure(ledger.epoch_index, str(e))
            self.tracker.save(self.run_dir)
            raise
        # Record the success BEFORE the self-audit: the commit is a fact even
        # if the audit errors out, and the retry path must see it.
        self.tracker.record_success(ledger.epoch_index, committed_at)
        self.tracker.save(self.run_dir)

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

    def _bundle_chain_id(self) -> int:
        if self._chain_id is not None:
            return self._chain_id
        return CHAIN_IDS.get(self.cfg.chain, 0)

    def _bundle_contract(self) -> str:
        return self.cfg.contract_address or "0x" + "00" * 20

    def _sign_evidence(self, typed: dict) -> dict | None:
        """EIP-712 signature over the evidence via the bridge (viem
        signTypedData with the operator key). Any bridge failure degrades to
        the deterministic unsigned fallback: evidence must never block a
        commit, and the zero signature is unmistakably not a claim."""
        try:
            out = self._call_bridge({"action": "sign_evidence", "typedData": typed})
        except CommitError as e:
            logger.warning("evidence signing failed (%s); bundle stays unsigned", e)
            return None
        if not out.get("signature") or not out.get("signer"):
            logger.warning("bridge returned no signature; bundle stays unsigned")
            return None
        return out

    def _evidence_uri(self, bundle_path: Path) -> str:
        """Stable relative URI (evidence/<run_id>/epoch-N.json) when the
        evidence root sits under the CWD, else the absolute path."""
        resolved = bundle_path.resolve()
        cwd = Path.cwd().resolve()
        try:
            return str(resolved.relative_to(cwd))
        except ValueError:
            return str(resolved)

    def _ipfs_add(self, bundle_path: Path) -> str | None:
        """Pin the bundle via a kubo RPC /api/v0/add (stdlib multipart POST).
        Publishing must never fail a commit: any error returns None."""
        try:
            data = bundle_path.read_bytes()
            boundary = f"covenant-{int(time.time() * 1000):x}"
            name = bundle_path.name
            body = (
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
                    f"Content-Type: application/json\r\n\r\n"
                ).encode()
                + data
                + f"\r\n--{boundary}--\r\n".encode()
            )
            req = urllib.request.Request(
                f"{self.cfg.ipfs_api.rstrip('/')}/api/v0/add?quiet=1",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                payload = json.loads(resp.read())
            cid = payload.get("Hash")
            if isinstance(cid, dict):  # kubo DAG-JSON shape: {"Hash": {"/": "cid"}}
                cid = cid.get("/")
            return str(cid) if cid else None
        except Exception as e:  # noqa: BLE001 - publishing is best-effort
            logger.warning("evidence publish to IPFS failed: %s", e)
            return None

    def _call_bridge(self, payload: dict) -> dict:
        out = call_bridge(payload)
        if "chainId" in out:
            with contextlib.suppress(TypeError, ValueError):
                self._chain_id = int(out["chainId"])
        return out
