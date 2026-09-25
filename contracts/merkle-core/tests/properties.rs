//! Deterministic property tests over random tree sizes 1..=64.
//!
//! The proptest RNG is seeded with a fixed value, so CI runs are fully
//! reproducible (the same shrinkable counterexample every time, and no
//! flaky drift). Runtime is bounded by construction: 64 cases x at most 64
//! leaves x O(log n) hashing per proof.

use alloy_primitives::{keccak256, B256};
use merkle_core::{build_proof, build_root, sorted_pair_hash, verify_proof};
use proptest::prelude::*;
use proptest::test_runner::{Config, RngSeed};

/// Fixed-seed, fixed-case-count config: deterministic on every machine (and
/// still overridable per-run via PROPTEST_CASES / PROPTEST_RNG_SEED).
fn fixed_config() -> Config {
    Config {
        cases: 64,
        rng_seed: RngSeed::Fixed(0x5EED_2026),
        ..Config::default()
    }
}

fn leaves_strategy() -> impl Strategy<Value = Vec<B256>> {
    (1usize..=64, any::<u64>()).prop_map(|(n, seed)| {
        (0..n)
            .map(|i| keccak256([seed.to_le_bytes(), (i as u64).to_le_bytes()].concat()))
            .collect()
    })
}

proptest! {
    #![proptest_config(fixed_config())]

    #[test]
    fn root_recomputation_is_deterministic(leaves in leaves_strategy()) {
        let root = build_root(&leaves);
        prop_assert_eq!(build_root(&leaves), root, "root must be a pure function of the leaves");
    }

    #[test]
    fn every_proof_verifies_against_the_root(leaves in leaves_strategy()) {
        let n = leaves.len();
        let root = build_root(&leaves);
        for (i, leaf) in leaves.iter().enumerate() {
            let proof = build_proof(&leaves, i);
            prop_assert!(verify_proof(*leaf, &proof, root), "n={n} i={i}");
            // Depth bound: the proof is exactly ceil(log2(n)) siblings, which
            // is what bounds on-chain verification cost (see docs/gas-notes.md).
            prop_assert_eq!(proof.len(), n.next_power_of_two().trailing_zeros() as usize);
        }
        // A foreign leaf must not verify against the committed root.
        let foreign = keccak256(b"covenant property test foreign leaf");
        prop_assert!(!verify_proof(foreign, &build_proof(&leaves, 0), root));
    }

    #[test]
    fn roots_agree_under_leaf_reordering_by_pairs(a in any::<u64>(), b in any::<u64>()) {
        let x = keccak256([a.to_le_bytes(), b.to_le_bytes()].concat());
        let y = keccak256([b.to_le_bytes(), a.to_le_bytes()].concat());
        prop_assert_eq!(build_root(&[x, y]), build_root(&[y, x]));
        prop_assert_eq!(sorted_pair_hash(x, y), sorted_pair_hash(y, x));
    }
}
