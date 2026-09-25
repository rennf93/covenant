"""Sorted-pair Merkle tree: the Python mirror of sdk/src/merkle.ts.

Semantics (must stay identical to contracts/src/merkle.rs and sdk/src/merkle.ts):
  - Leaves are 32-byte receipt hashes.
  - Internal node = keccak256(min ++ max), byte-wise unsigned comparison on the
    32-byte values, so proofs are position-free.
  - A level with an odd node count is padded by DUPLICATING its last node
    inside the hash input only; the stored level keeps the original count.
  - A single-leaf tree has root == leaf and an empty proof.
  - An empty epoch must not commit a tree: the contract stores bytes32(0).

Conformance is enforced by tests against the shared cross-language fixtures
in contracts/merkle-core/tests/fixtures/merkle.json.

Layering: leaf (pure python + _keccak). Attest must never import
vouch.engine, vouch.venues, or vouch.server.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._keccak import keccak256

ZERO32 = "0x" + "00" * 32
Hash = str  # 0x-prefixed lowercase 32-byte hex


class MerkleError(ValueError):
    pass


def _unhex(h: Hash) -> bytes:
    if not isinstance(h, str) or not h.startswith("0x") or len(h) != 66:
        raise MerkleError(f"expected 0x-prefixed 32-byte hex, got: {h!r}")
    try:
        return bytes.fromhex(h[2:])
    except ValueError:
        raise MerkleError(f"invalid hex in hash: {h!r}") from None


def _sorted_pair_hash(a: Hash, b: Hash) -> Hash:
    lo, hi = (a, b) if a.lower() < b.lower() else (b, a)
    return "0x" + keccak256(_unhex(lo) + _unhex(hi)).hex()


@dataclass
class MerkleTree:
    leaves: list[Hash]
    root: Hash
    _sibling_levels: list[list[Hash | None]] = field(repr=False)
    _level_counts: list[int] = field(repr=False)

    def proof(self, i: int) -> list[Hash]:
        """Sibling list, leaf to root, for the leaf at index i."""
        if not isinstance(i, int) or i < 0 or i >= len(self.leaves):
            raise MerkleError(f"leaf index {i} out of range")
        out: list[Hash] = []
        idx = i
        for level, siblings in enumerate(self._sibling_levels):
            if idx < self._level_counts[level]:
                sib = siblings[idx]
                if sib is not None:
                    out.append(sib)
            idx //= 2
        return out


def build_merkle_tree(leaves: list[Hash]) -> MerkleTree:
    if not leaves:
        raise MerkleError("empty epoch: an epoch with zero receipts must not commit a tree")
    current = list(leaves)
    sibling_levels: list[list[Hash | None]] = []
    level_counts: list[int] = []
    while len(current) > 1:
        nxt: list[Hash] = []
        siblings: list[Hash | None] = [None] * len(current)
        for i in range(0, len(current), 2):
            left = current[i]
            right = current[i + 1] if i + 1 < len(current) else left  # duplicate-last padding
            nxt.append(_sorted_pair_hash(left, right))
            siblings[i] = right
            if i + 1 < len(current):
                siblings[i + 1] = left
        sibling_levels.append(siblings)
        level_counts.append(len(current))
        current = nxt
    return MerkleTree(
        leaves=list(leaves),
        root=current[0],
        _sibling_levels=sibling_levels,
        _level_counts=level_counts,
    )


def verify_proof(leaf: Hash, proof: list[Hash], root: Hash) -> bool:
    """Position-free proof verification (mirror of merkle.ts verifyProof)."""
    try:
        current = _unhex(leaf)
        for p in proof:
            pb = _unhex(p)
            lo, hi = (current, pb) if current < pb else (pb, current)
            current = keccak256(lo + hi)
    except MerkleError:
        return False
    return "0x" + current.hex() == root.lower()
