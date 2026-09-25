"""Canonical trade receipts: the Python mirror of sdk/src/receipt.ts.

The TS module is the single source of truth for the encoding; this file must
stay byte-identical in output. Conformance is enforced by
tests/test_attest.py against fixture vectors generated from the SDK
(tests/fixtures/receipts.json). Any change here is a protocol change and
must be mirrored upstream and re-verified against fresh fixtures.

Encoding (all static ABI types, so the encoding is a bare concatenation of
32-byte big-endian words):

    uint256 strategyId, uint64 epochIndex, uint8 venue, bytes32 venueOrderIdHash,
    bytes32 instrumentHash, uint8 side, int256 sizeSignedBase, uint256 priceQuote,
    uint256 feeQuote, uint64 filledAt

Then leaf hash = keccak256(encoding).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ._keccak import keccak256_hex

# Venue classes (sdk/src/receipt.ts enum Venue).
VENUE_COINBASE = 0
VENUE_ARBITRUM_DEX = 1
VENUE_OTHER = 2

SIDE_BUY = 0
SIDE_SELL = 1

_UINT256_MAX = 2**256 - 1
_INT256_MAX = 2**255 - 1
_INT256_MIN = -(2**255)
_UINT64_MAX = 2**64 - 1

_DECIMAL_RE = re.compile(r"^(-?)(\d+)(?:\.(\d+))?$")


class ReceiptError(ValueError):
    """Structurally invalid receipt or unparseable fixed-point decimal."""


@dataclass
class Receipt:
    """Canonical receipt with hashable integer fields (see receipt.ts)."""

    strategy_id: int
    epoch_index: int
    venue: int
    venue_order_id_hash: str  # 0x-prefixed bytes32
    instrument_hash: str      # 0x-prefixed bytes32
    side: int
    size_signed_base: int     # int256, positive on Buy, negative on Sell
    price_quote: int          # uint256, 8-decimals fixed point
    fee_quote: int            # uint256, 8-decimals fixed point
    filled_at: int            # unix seconds

    def to_dict(self) -> dict:
        return {
            "strategyId": self.strategy_id,
            "epochIndex": self.epoch_index,
            "venue": self.venue,
            "venueOrderIdHash": self.venue_order_id_hash,
            "instrumentHash": self.instrument_hash,
            "side": self.side,
            "sizeSignedBase": self.size_signed_base,
            "priceQuote": self.price_quote,
            "feeQuote": self.fee_quote,
            "filledAt": self.filled_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Receipt":
        return cls(
            strategy_id=int(d["strategyId"]),
            epoch_index=int(d["epochIndex"]),
            venue=int(d["venue"]),
            venue_order_id_hash=d["venueOrderIdHash"],
            instrument_hash=d["instrumentHash"],
            side=int(d["side"]),
            size_signed_base=int(d["sizeSignedBase"]),
            price_quote=int(d["priceQuote"]),
            fee_quote=int(d["feeQuote"]),
            filled_at=int(d["filledAt"]),
        )


def validate_receipt(r: Receipt) -> list[str]:
    """Invariants the ABI cannot express; mirrors receipt.ts validateReceipt."""
    problems: list[str] = []
    if r.strategy_id < 0:
        problems.append("strategyId must be non-negative")
    if r.epoch_index < 0:
        problems.append("epochIndex must be non-negative")
    if r.filled_at < 0 or r.filled_at > _UINT64_MAX:
        problems.append("filledAt must fit uint64")
    if r.price_quote > _UINT256_MAX:
        problems.append("priceQuote must fit uint256")
    if r.fee_quote > _UINT256_MAX:
        problems.append("feeQuote must fit uint256")
    if r.size_signed_base > _INT256_MAX or r.size_signed_base < _INT256_MIN:
        problems.append("sizeSignedBase must fit int256")
    if r.side == SIDE_BUY and r.size_signed_base <= 0:
        problems.append("Buy receipt must carry positive size")
    if r.side == SIDE_SELL and r.size_signed_base >= 0:
        problems.append("Sell receipt must carry negative size")
    if r.price_quote == 0:
        problems.append("priceQuote must be positive")
    return problems


def encode_receipt(r: Receipt) -> str:
    """ABI-encode the receipt exactly as the contract and SDK expect."""
    problems = validate_receipt(r)
    if problems:
        raise ReceiptError("invalid receipt: " + "; ".join(problems))
    words = [
        r.strategy_id,                       # uint256
        r.epoch_index,                       # uint64 (static, padded like any int)
        r.venue,                             # uint8
        _bytes32_word(r.venue_order_id_hash),
        _bytes32_word(r.instrument_hash),
        r.side,                              # uint8
        r.size_signed_base & (2**256 - 1),   # int256, two's complement
        r.price_quote,                       # uint256
        r.fee_quote,                         # uint256
        r.filled_at,                         # uint64
    ]
    return "0x" + b"".join(w.to_bytes(32, "big") for w in words).hex()


def receipt_hash(r: Receipt) -> str:
    """Canonical leaf hash. Raises on structurally invalid receipts."""
    return keccak256_hex(bytes.fromhex(encode_receipt(r)[2:]))


def hash_of_string(s: str) -> str:
    """keccak256(utf8(s)): used for venueOrderIdHash and instrumentHash."""
    return keccak256_hex(s.encode("utf-8"))


def parse_fixed8(x: int | float | str) -> int:
    """Parse a decimal into 8-decimals fixed point, exactly.

    Mirrors receipt.ts parseFixed8: at most 8 fractional digits, no
    floating-point rounding. Floats are stringified first (repr), which is
    exact for the human-unit prices adapters pass in.
    """
    s = repr(x) if isinstance(x, float) else str(x)
    m = _DECIMAL_RE.match(s)
    if not m:
        raise ReceiptError(f"invalid decimal: {s}")
    if len(m.group(3) or "") > 8:
        raise ReceiptError(f"more than 8 fractional digits would lose precision: {s}")
    sign = -1 if m.group(1) == "-" else 1
    whole = m.group(2)
    frac = (m.group(3) or "").ljust(8, "0")
    return sign * (int(whole) * 100_000_000 + int(frac or "0"))


def make_receipt(
    *,
    strategy_id: int,
    epoch_index: int,
    venue: int,
    venue_order_id: str,
    instrument: str,
    side: int,
    size_base: int | float | str,
    price: int | float | str,
    fee: int | float | str,
    filled_at: int,
) -> Receipt:
    """Build a receipt from human units (mirror of receipt.ts makeReceipt).

    Sign convention: Buy receipts carry positive size, Sell receipts carry
    negative size. Adapters must pass side and a positive size_base; the
    sign is derived here so the two can never disagree.
    """
    if side not in (SIDE_BUY, SIDE_SELL):
        raise ReceiptError(f"side must be {SIDE_BUY} (Buy) or {SIDE_SELL} (Sell)")
    try:
        magnitude = parse_fixed8(size_base)
    except ReceiptError:
        raise
    signed = magnitude if side == SIDE_BUY else -magnitude
    return Receipt(
        strategy_id=strategy_id,
        epoch_index=epoch_index,
        venue=venue,
        venue_order_id_hash=hash_of_string(venue_order_id),
        instrument_hash=hash_of_string(instrument),
        side=side,
        size_signed_base=signed,
        price_quote=parse_fixed8(price),
        fee_quote=parse_fixed8(fee),
        filled_at=filled_at,
    )


def _bytes32_word(h: str) -> int:
    if not isinstance(h, str) or not h.startswith("0x") or len(h) != 66:
        raise ReceiptError(f"expected 0x-prefixed 32-byte hex, got: {h!r}")
    try:
        return int.from_bytes(bytes.fromhex(h[2:]), "big")
    except ValueError:
        raise ReceiptError(f"invalid hex in bytes32: {h!r}") from None


def usdg_from_usd(amount_usd: float | str | Decimal) -> int:
    """Convert a human USD amount to USDG base units (6 decimals), half-up.

    USDG tracks the dollar; the paper broker's equity is already in USD, so
    the closing mark conversion is 1:1 with 6-decimal truncation-free
    rounding. Real-venue balances must be converted at the epoch's closing
    mark BEFORE reaching this function.
    """
    d = Decimal(str(amount_usd))
    scaled = d * Decimal(10) ** 6
    # half-up rounding on the absolute value, sign reapplied (Decimal's
    # ROUND_HALF_UP is symmetric away-from-zero, which is what we want)
    from decimal import ROUND_HALF_UP

    return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP))
