#!/usr/bin/env python
"""One-time strategy registration against the Covenant contract.

    COVENANT_ATTEST=1 COVENANT_RPC_URL=... COVENANT_PRIVATE_KEY=... \
        COVENANT_CONTRACT_ADDRESS=... python run_register.py \
        --name vouch-sol --metadata-uri ipfs://bafy...

Registers `registerStrategy(name, metadata_uri)` through the same Node bridge
the attestor uses, prints the strategy id and the exact env line to set. The
connection env trio is REQUIRED: this is a bond-collateralizing chain write,
never a dry-run. Defaults come from config when flags are omitted
(COVENANT_STRATEGY_NAME / COVENANT_STRATEGY_METADATA_URI).
"""

from __future__ import annotations

import argparse
import sys

from vouch.attest.commit import AttestationConfig, call_bridge
from vouch.config import load_settings


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="register a Covenant strategy; prints the env line")
    ap.add_argument("--name", type=str, default=None, help="strategy name (default: config)")
    ap.add_argument(
        "--metadata-uri",
        type=str,
        default=None,
        help="metadata URI (default: COVENANT_STRATEGY_METADATA_URI or empty)",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    cfg = AttestationConfig.from_env()
    if not cfg.enabled:
        print("Refusing: attestation is off (COVENANT_ATTEST != 1).", file=sys.stderr)
        return 2
    if not cfg.connected:
        print(
            "Refusing: registration is a chain write and needs the connection trio "
            "COVENANT_RPC_URL, COVENANT_PRIVATE_KEY, COVENANT_CONTRACT_ADDRESS.",
            file=sys.stderr,
        )
        return 2

    attest = load_settings().attest
    name = args.name if args.name is not None else attest.strategy_name
    metadata_uri = (
        args.metadata_uri if args.metadata_uri is not None else attest.strategy_metadata_uri
    )

    out = call_bridge({"action": "register", "name": name, "metadataUri": metadata_uri})
    strategy_id = out.get("strategyId")
    if strategy_id is None:
        print(
            f"Registration tx {out.get('hash', '?')} mined but no StrategyRegistered id "
            "could be read from the receipt logs; check the tx in the explorer and set "
            "COVENANT_STRATEGY_ID by hand.",
            file=sys.stderr,
        )
        return 1

    print(f"strategy registered: id {strategy_id} name {name!r} tx {out.get('hash', '?')}")
    print("Set this in the environment of every attested run (shadow/real/register):")
    print(f"  export COVENANT_STRATEGY_ID={strategy_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
