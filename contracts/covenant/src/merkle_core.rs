//! Canonical receipt hashing and sorted-pair Merkle trees.
//!
//! Semantics (MUST stay identical to sdk/src/merkle.ts):
//!  - Leaves are 32-byte receipt hashes.
//!  - Internal node = keccak256(min ++ max), byte-wise unsigned comparison.
//!    Proof verification never needs leaf positions.
//!  - A level with an odd node count is padded by duplicating its last node
//!    inside the hash input only.
//!  - A single-leaf tree has root == leaf, proof == [].
//!  - An empty epoch must not commit a tree; callers store B256::ZERO and
//!    verification against it always fails.

use alloy_primitives::{B256, keccak256};

/// Internal node hash: keccak256 of the pair concatenated in sorted order.
pub fn sorted_pair_hash(a: B256, b: B256) -> B256 {
    if a <= b {
        keccak256([a.as_slice(), b.as_slice()].concat())
    } else {
        keccak256([b.as_slice(), a.as_slice()].concat())
    }
}

/// Build the Merkle root for `leaves` (insertion order).
/// Panics on empty input: empty epochs must not commit trees.
pub fn build_root(leaves: &[B256]) -> B256 {
    assert!(!leaves.is_empty(), "empty epoch: must not commit a tree");
    let mut current: Vec<B256> = leaves.to_vec();
    while current.len() > 1 {
        let mut next = Vec::with_capacity(current.len().div_ceil(2));
        for pair in current.chunks(2) {
            let left = pair[0];
            // Duplicate-last padding for odd-length levels.
            let right = pair.get(1).copied().unwrap_or(left);
            next.push(sorted_pair_hash(left, right));
        }
        current = next;
    }
    current[0]
}

/// Sibling list, leaf to root, for the leaf at `index`.
/// Panics on empty input or out-of-range index.
pub fn build_proof(leaves: &[B256], index: usize) -> Vec<B256> {
    assert!(!leaves.is_empty(), "empty epoch: must not commit a tree");
    assert!(index < leaves.len(), "leaf index out of range");
    let mut current: Vec<B256> = leaves.to_vec();
    let mut idx = index;
    let mut proof = Vec::new();
    while current.len() > 1 {
        let sibling_idx = if idx % 2 == 1 { idx - 1 } else { idx + 1 };
        // With duplicate-last padding, a last-position leaf on an odd-length
        // level is its own sibling (its hash input used the node twice).
        proof.push(current[sibling_idx.min(current.len() - 1)]);
        let mut next = Vec::with_capacity(current.len().div_ceil(2));
        for pair in current.chunks(2) {
            let left = pair[0];
            let right = pair.get(1).copied().unwrap_or(left);
            next.push(sorted_pair_hash(left, right));
        }
        current = next;
        idx /= 2;
    }
    proof
}

/// Verify `leaf` against `root` given its sibling proof.
/// Mirrors sdk/src/merkle.ts::verifyProof exactly.
pub fn verify_proof(leaf: B256, proof: &[B256], root: B256) -> bool {
    let mut computed = leaf;
    for sibling in proof {
        computed = sorted_pair_hash(computed, *sibling);
    }
    computed == root
}

#[cfg(test)]
mod tests {
    use super::*;

    fn leaf(seed: u8) -> B256 {
        keccak256([seed; 32])
    }

    #[test]
    fn single_leaf_root_equals_leaf_with_empty_proof() {
        let l = leaf(1);
        assert_eq!(build_root(&[l]), l);
        assert!(build_proof(&[l], 0).is_empty());
        assert!(verify_proof(l, &[], l));
    }

    #[test]
    fn proofs_verify_for_every_leaf_at_several_sizes() {
        for n in [2usize, 3, 4, 5, 7, 8, 13, 16, 41] {
            let leaves: Vec<B256> = (0..n as u8).map(leaf).collect();
            let root = build_root(&leaves);
            for i in 0..n {
                let proof = build_proof(&leaves, i);
                assert!(
                    verify_proof(leaves[i], &proof, root),
                    "proof failed n={n} i={i}"
                );
            }
            assert!(!verify_proof(leaf(0xFF), &build_proof(&leaves, 0), root));
        }
    }

    #[test]
    #[should_panic(expected = "empty epoch")]
    fn empty_input_panics() {
        let _ = build_root(&[]);
    }

    #[test]
    fn root_is_content_sensitive() {
        let a = build_root(&[leaf(1), leaf(2)]);
        let b = build_root(&[leaf(1), leaf(3)]);
        assert_ne!(a, b);
    }

    #[test]
    fn order_independent_pairs() {
        let a = leaf(1);
        let b = leaf(2);
        assert_eq!(sorted_pair_hash(a, b), sorted_pair_hash(b, a));
    }
}
