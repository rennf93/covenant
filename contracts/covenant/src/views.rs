//! Read-only views. Existence sentinels: a zero owner means an unregistered
//! strategy; checkpoint existence is checked against the strategy's
//! `epoch_count` (never `committed_at`, which is legitimately 0 at VM time 0).

use alloc::string::String;

use alloy_primitives::{Address, B256, I256, U8, U64, U256};

use crate::storage::Covenant;
use crate::types::*;

// Free functions so the single #[public] impl in lib.rs stays thin;
// this module owns the logic.
pub(crate) fn strategy_count(c: &Covenant) -> U256 {
    c.strategy_count.get()
}

pub(crate) fn config(c: &Covenant) -> (Address, Address, Address, U256, U256, U64, bool, U256) {
    (
        c.admin.get(),
        c.resolver.get(),
        c.usdg.get(),
        c.bond_amount.get(),
        c.challenge_stake.get(),
        c.challenge_window.get(),
        c.paused.get(),
        c.treasury.get(),
    )
}

pub(crate) fn get_strategy(
    c: &Covenant,
    strategy_id: U256,
) -> Result<(Address, String, String, U256, U8, U64), CovenantError> {
    let strategy = c.strategies.getter(strategy_id);
    if strategy.owner.get().is_zero() {
        return Err(CovenantError::StrategyNotFound(StrategyNotFound {}));
    }
    Ok((
        strategy.owner.get(),
        strategy.name.get_string(),
        strategy.metadata_uri.get_string(),
        strategy.bond.get(),
        strategy.status.get(),
        strategy.created_at.get(),
    ))
}

pub(crate) fn get_performance(
    c: &Covenant,
    strategy_id: U256,
) -> Result<(I256, I256, I256, U64, U64), CovenantError> {
    let strategy = c.strategies.getter(strategy_id);
    if strategy.owner.get().is_zero() {
        return Err(CovenantError::StrategyNotFound(StrategyNotFound {}));
    }
    Ok((
        strategy.equity.get(),
        strategy.high_water_mark.get(),
        strategy.cumulative_pnl.get(),
        strategy.epoch_count.get(),
        strategy.finalized_epochs.get(),
    ))
}

/// ABI return shapes are positional tuples by design (clippy's
/// type_complexity does not apply to Solidity views).
#[allow(clippy::type_complexity)]
pub(crate) fn get_checkpoint(
    c: &Covenant,
    strategy_id: U256,
    epoch_index: U64,
) -> Result<(I256, I256, B256, String, U8, U64, Address, U256), CovenantError> {
    if epoch_index >= c.strategies.getter(strategy_id).epoch_count.get() {
        return Err(CovenantError::EpochUnknown(EpochUnknown {}));
    }
    let epoch_map = c.checkpoints.getter(strategy_id);
    let checkpoint = epoch_map.get(epoch_index);
    Ok((
        checkpoint.equity.get(),
        checkpoint.net_flow.get(),
        checkpoint.trades_root.get(),
        checkpoint.evidence_uri.get_string(),
        checkpoint.status.get(),
        checkpoint.committed_at.get(),
        checkpoint.challenger.get(),
        checkpoint.stake.get(),
    ))
}
