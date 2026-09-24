import assert from "node:assert/strict";
import { test } from "node:test";
import { keccak256, toHex, type Hash } from "viem";
import {
  EpochBuilder,
  Venue,
  Side,
  makeReceipt,
  receiptHash,
  parseFixed8,
  validateReceipt,
} from "../src/index.js";
import { buildMerkleTree, verifyProof } from "../src/merkle.js";

const SID = 1n;

function sampleReceipt(overrides: Partial<Parameters<typeof makeReceipt>[0]> = {}) {
  return makeReceipt({
    strategyId: SID,
    epochIndex: 0n,
    venue: Venue.ArbitrumDex,
    venueOrderId: "0xorder-1",
    instrument: "SOL/USDC",
    side: Side.Buy,
    sizeBase: 1.25,
    price: 147.32,
    fee: 0.15,
    filledAt: 1_758_700_000,
    ...overrides,
  });
}

function fixed8(x: string): bigint {
  const [whole, frac = ""] = x.split(".");
  return BigInt(whole! + frac!.padEnd(8, "0"));
}

test("parseFixed8 is exact", () => {
  assert.equal(parseFixed8("147.32"), fixed8("147.32"));
  assert.equal(parseFixed8("0.00000001"), 1n);
  assert.equal(parseFixed8("-1.5"), -150_000_000n);
  assert.equal(parseFixed8(2), 200_000_000n);
  assert.throws(() => parseFixed8("1.234567890"), /lose precision/);
});

test("receipt hash is stable and side/size sign is enforced", () => {
  const r = sampleReceipt();
  const h1 = receiptHash(r);
  const h2 = receiptHash({ ...r });
  assert.equal(h1, h2);
  assert.match(h1, /^0x[0-9a-f]{64}$/);

  assert.throws(() => receiptHash(sampleReceipt({ sizeBase: -1 })), /positive size/);
  // Sell with negative size is VALID (side and size sign agree); a Sell whose
  // computed signed size is non-negative is the protocol violation.
  assert.throws(() => receiptHash(sampleReceipt({ side: Side.Sell, sizeBase: 0 })), /negative size/);
  assert.throws(() => receiptHash(sampleReceipt({ price: 0 })), /positive/);

  const errors = validateReceipt({ ...sampleReceipt(), filledAt: 2n ** 64n });
  assert.ok(errors.length > 0);
});

test("same receipt data with different raw venue order ids hashes differently", () => {
  const a = receiptHash(sampleReceipt({ venueOrderId: "order-A" }));
  const b = receiptHash(sampleReceipt({ venueOrderId: "order-B" }));
  assert.notEqual(a, b);
});

test("merkle: single leaf root equals leaf with empty proof", () => {
  const leaf = receiptHash(sampleReceipt());
  const tree = buildMerkleTree([leaf]);
  assert.equal(tree.root, leaf);
  assert.deepEqual(tree.proof(0), []);
  assert.ok(verifyProof(leaf, [], tree.root));
});

test("merkle: proofs verify for every leaf at several sizes", () => {
  for (const n of [2, 3, 4, 5, 7, 8, 13, 16]) {
    const leaves: Hash[] = [];
    for (let i = 0; i < n; i++) {
      leaves.push(receiptHash(sampleReceipt({ venueOrderId: `order-${n}-${i}`, filledAt: 1_758_700_000 + i })));
    }
    const tree = buildMerkleTree(leaves);
    for (let i = 0; i < n; i++) {
      assert.ok(
        verifyProof(leaves[i]!, tree.proof(i), tree.root),
        `proof failed n=${n} i=${i}`,
      );
    }
    assert.ok(!verifyProof(leaves[0]!, tree.proof(0), keccak256(toHex("not the root"))));
  }
});

test("merkle: tampering with any byte of a sibling breaks verification", () => {
  const leaves = [receiptHash(sampleReceipt({ venueOrderId: "a" })), receiptHash(sampleReceipt({ venueOrderId: "b", filledAt: 2 })) ];
  const tree = buildMerkleTree(leaves);
  const proof = tree.proof(0);
  const bad = proof.map((p) => (p.endsWith("0") ? (p.slice(0, -1) + "1") as Hash : (p.slice(0, -1) + "0") as Hash));
  assert.ok(!verifyProof(leaves[0]!, bad, tree.root));
});

test("epoch builder enforces uniqueness, ordering, and identity", () => {
  const b = new EpochBuilder(SID, 3n);
  const ep = { epochIndex: 3n };
  b.add(sampleReceipt({ venueOrderId: "o1", filledAt: 100, ...ep }));
  b.add(sampleReceipt({ venueOrderId: "o2", filledAt: 101, side: Side.Sell, sizeBase: 0.5, ...ep }));
  assert.equal(b.size, 2);

  assert.throws(() => b.add(sampleReceipt({ venueOrderId: "o1", filledAt: 102, ...ep })), /duplicate/);
  assert.throws(() => b.add(sampleReceipt({ venueOrderId: "o3", filledAt: 100, ...ep })), /precedes/);
  assert.throws(
    () => b.add(sampleReceipt({ venueOrderId: "o4", filledAt: 200, strategyId: 99n, ...ep })),
    /strategyId/,
  );
  assert.throws(
    () => new EpochBuilder(SID, 5n).commitPayload({ equityUsdg: 1n, netFlowUsdg: 0n, evidenceURI: "ipfs://x" }),
    /zero receipts/,
  );

  const payload = b.commitPayload({ equityUsdg: 1_000_000000n, netFlowUsdg: 0n, evidenceURI: "ipfs://log" });
  assert.equal(payload.tradesRoot, payload.tree.root);
  assert.equal(payload.receipts.length, 2);
  for (let i = 0; i < payload.receipts.length; i++) {
    assert.ok(verifyProof(payload.receipts[i]!, payload.tree.proof(i), payload.tradesRoot));
  }
});

test("property: random trees, all proofs verify, roots differ per content", () => {
  let seed = 0xc0ffee;
  const rand = () => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed / 0x7fffffff;
  };
  const roots = new Set<string>();
  for (let trial = 0; trial < 25; trial++) {
    const n = 1 + Math.floor(rand() * 40);
    const leaves: Hash[] = [];
    const b = new EpochBuilder(SID, BigInt(trial));
    for (let i = 0; i < n; i++) {
      const r = sampleReceipt({
        venueOrderId: `t${trial}-${i}`,
        sizeBase: (0.001 + rand() * 10).toFixed(8),
        price: (1 + rand() * 1000).toFixed(8),
        fee: rand().toFixed(8),
        filledAt: 1_758_700_000 + i,
        side: Side.Buy,
        strategyId: SID,
        epochIndex: BigInt(trial),
      });
      b.add(r);
      leaves.push(receiptHash(r));
    }
    const tree = buildMerkleTree(leaves);
    roots.add(tree.root);
    for (let i = 0; i < n; i++) {
      assert.ok(verifyProof(leaves[i]!, tree.proof(i), tree.root), `trial=${trial} i=${i}`);
    }
    // a leaf from another tree must not verify against this root
    const foreign = receiptHash(sampleReceipt({ venueOrderId: `foreign-${trial}` }));
    assert.ok(!verifyProof(foreign, tree.proof(0), tree.root));
  }
  assert.ok(roots.size === 25, "roots must be content-unique");
});
