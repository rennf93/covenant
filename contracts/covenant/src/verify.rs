//! Onchain receipt verification: Merkle proofs against committed epoch roots.

use alloc::vec::Vec;

use alloy_primitives::{B256, U64, U256};

use crate::merkle_core::verify_proof;

use crate::storage::Covenant;

// Free functions so the single #[public] impl in lib.rs stays thin;
// this module owns the logic.
/// Verifies a canonical receipt hash against a committed epoch root.
/// Returns false for unknown epochs and zero roots (empty epochs).
pub(crate) fn verify_receipt(
    c: &Covenant,
    strategy_id: U256,
    epoch_index: U64,
    proof: Vec<B256>,
    receipt: B256,
) -> bool {
    let epoch_map = c.checkpoints.getter(strategy_id);
    let root = epoch_map.get(epoch_index).trades_root.get();
    if root == B256::ZERO {
        return false;
    }
    verify_proof(receipt, &proof, root)
}
