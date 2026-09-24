import { concat, keccak256, type Hash } from "viem";

/**
 * Sorted-pair Merkle tree over receipt hashes (OpenZeppelin-style semantics).
 *
 * Semantics (MUST stay identical to contracts/src/merkle.rs):
 *  - Leaves are 32-byte receipt hashes.
 *  - Internal node = keccak256(min ++ max), byte-wise unsigned comparison on the
 *    32-byte values. Proof verification therefore never needs leaf positions.
 *  - A level with an odd node count is padded by DUPLICATING its last node
 *    before hashing (the duplicate exists only inside the hash input; the
 *    stored level keeps the original count for proof generation).
 *  - A single-leaf tree has root == leaf, proof == [].
 *  - An empty epoch must not commit a tree: the contract stores bytes32(0)
 *    and verify always returns false against it.
 *  - Duplicated leaf values are technically verifiable, so adapters MUST reject
 *    duplicate receipts within an epoch (see EpochBuilder).
 */
export interface MerkleTree {
  /** Leaves, in insertion order. */
  readonly leaves: readonly Hash[];
  /** Root hash. */
  readonly root: Hash;
  /** Sibling list, leaf to root, for the leaf at index i. */
  proof(i: number): Hash[];
}

function sortedPairHash(a: Hash, b: Hash): Hash {
  const [lo, hi] = a.toLowerCase() < b.toLowerCase() ? [a, b] : [b, a];
  return keccak256(concat([lo, hi]));
}

export function buildMerkleTree(leaves: readonly Hash[]): MerkleTree {
  if (leaves.length === 0) {
    throw new Error("empty epoch: an epoch with zero receipts must not commit a tree");
  }
  let current: Hash[] = [...leaves];
  /** siblingsPerLevel[i] = sibling list for level i, parallel to proof(i). */
  const siblingLevels: (Hash | undefined)[][] = [];
  const levelsForSiblings: Hash[][] = [];

  while (current.length > 1) {
    const next: Hash[] = [];
    const siblings: (Hash | undefined)[] = new Array(current.length).fill(undefined);
    for (let i = 0; i < current.length; i += 2) {
      const left = current[i]!;
      const right = i + 1 < current.length ? current[i + 1]! : left; // duplicate-last padding
      next.push(sortedPairHash(left, right));
      siblings[i] = right;
      siblings[i + 1] = left;
    }
    siblingLevels.push(siblings);
    levelsForSiblings.push(current);
    current = next;
  }

  const root = current[0]!;
  return {
    leaves,
    root,
    proof(i: number): Hash[] {
      if (!Number.isInteger(i) || i < 0 || i >= leaves.length) {
        throw new Error(`leaf index ${i} out of range`);
      }
      const proof: Hash[] = [];
      let idx = i;
      for (let level = 0; level < siblingLevels.length; level++) {
        const sib = siblingLevels[level]![idx];
        if (sib !== undefined) proof.push(sib);
        idx = Math.floor(idx / 2);
      }
      return proof;
    },
  };
}

/**
 * Verify a leaf against a committed root given its sibling proof.
 * Mirrors contracts/src/merkle.rs::verify exactly.
 */
export function verifyProof(leaf: Hash, proof: readonly Hash[], root: Hash): boolean {
  let computed = leaf;
  for (const sibling of proof) {
    computed = sortedPairHash(computed, sibling);
  }
  return computed.toLowerCase() === root.toLowerCase();
}
