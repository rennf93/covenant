"""Pure-Python Keccak-256 (the Ethereum variant, NOT NIST SHA3).

The attestation module must hash canonical receipts exactly like the SDK
(viem keccak256) and the Stylus contract, and the operator environment has
no crypto dependencies beyond httpx. This is the classic Keccak-f[1600]
reference algorithm with the original Keccak padding (domain byte 0x01,
final byte 0x80). Conformance is enforced by cross-language fixture tests
against vectors generated with the SDK (tests/fixtures/receipts.json) and
the shared Merkle fixtures (contracts/merkle-core/tests/fixtures/merkle.json).
"""

from __future__ import annotations

_MASK = 0xFFFFFFFFFFFFFFFF
_RATE = 136  # bytes: 1600/8 - 2*256/8

_ROUND_CONSTANTS = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]


def _rotl64(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _MASK


def _keccak_f1600(a: list[list[int]]) -> list[list[int]]:
    """One permutation round set over the 5x5 lane state, a[x][y]."""
    for rc in _ROUND_CONSTANTS:
        # theta
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        # rho and pi
        x, y = 1, 0
        current = a[x][y]
        for t in range(24):
            x, y = y, (2 * x + 3 * y) % 5
            current, a[x][y] = a[x][y], _rotl64(current, (t + 1) * (t + 2) // 2 % 64)
        # chi
        for y in range(5):
            t = [a[x][y] for x in range(5)]
            for x in range(5):
                a[x][y] = t[x] ^ ((~t[(x + 1) % 5]) & t[(x + 2) % 5]) & _MASK
        # iota
        a[0][0] ^= rc
    return a


def keccak256(data: bytes) -> bytes:
    """Keccak-256 digest of `data` (Ethereum semantics)."""
    # state lanes indexed [x][y]; lane i of the flat state maps to (i % 5, i // 5)
    a = [[0] * 5 for _ in range(5)]

    # absorb full blocks
    offset = 0
    while len(data) - offset >= _RATE:
        _absorb_block(a, data[offset:offset + _RATE])
        offset += _RATE

    # final block with original Keccak padding: 0x01 ... 0x80
    tail = bytearray(data[offset:])
    pad_len = _RATE - len(tail)
    tail += b"\x00" * pad_len
    tail[len(data) - offset] ^= 0x01
    tail[_RATE - 1] ^= 0x80
    _absorb_block(a, bytes(tail))

    # squeeze 32 bytes (fits in one block, rate is 136)
    out = bytearray()
    for i in range(4):  # 4 lanes x 8 bytes = 32
        lane = a[i % 5][i // 5]
        out += lane.to_bytes(8, "little")
    return bytes(out)


def _absorb_block(a: list[list[int]], block: bytes) -> None:
    for i in range(_RATE // 8):
        lane = int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        a[i % 5][i // 5] ^= lane
    _keccak_f1600(a)


def keccak256_hex(data: bytes) -> str:
    """0x-prefixed lowercase hex digest, matching the SDK's Hash type."""
    return "0x" + keccak256(data).hex()
