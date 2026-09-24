/**
 * Generates cross-language test fixtures: JSON vectors that the Rust
 * merkle-core test suite verifies, guaranteeing TS and Rust Merkle
 * semantics stay identical. Run: pnpm run gen-fixtures
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { keccak256, toHex, type Hash } from "viem";
import { buildMerkleTree } from "../src/merkle.ts";

function leaf(seed: number): Hash {
  return keccak256(new Uint8Array(32).fill(seed));
}

const sizes = [1, 2, 3, 4, 5, 7, 8, 13, 16, 41, 100];

const fixtures = sizes.map((n) => {
  const leaves = Array.from({ length: n }, (_, i) => leaf((i * 37 + n) % 256));
  const tree = buildMerkleTree(leaves);
  const cases = leaves.map((leafValue, i) => ({
    leaf: leafValue,
    proof: tree.proof(i),
    valid: true,
  }));
  // negative cases: wrong leaf / corrupted proof / wrong root
  cases.push({ leaf: leaf(0xff), proof: tree.proof(0), valid: false, wrongRoot: false });
  if (n > 1) {
    // A corrupted proof is only meaningful when the proof is non-empty
    // (a single-leaf tree's empty proof trivially verifies its only leaf).
    const corrupted = tree.proof(0).map((p) =>
      keccak256(new Uint8Array([...new Uint8Array(32).fill(0xab), ...new Uint8Array(0)])),
    );
    cases.push({ leaf: leaves[0]!, proof: corrupted, valid: false, wrongRoot: false });
  }
  return {
    size: n,
    leaves,
    root: tree.root,
    cases,
  };
});

const outDir = join(import.meta.dirname, "../../contracts/merkle-core/tests/fixtures");
mkdirSync(outDir, { recursive: true });
writeFileSync(
  join(outDir, "merkle.json"),
  JSON.stringify({ algorithm: "sorted-pair, duplicate-last padding, keccak256", fixtures }, null, 2),
);
console.log(`wrote fixtures for ${sizes.length} tree sizes`);
