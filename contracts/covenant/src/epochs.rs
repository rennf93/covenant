//! Epoch lifecycle: committing checkpoints and finalizing them once the
//! challenge window has elapsed. Performance accounting happens ONLY at
//! finalization (PnL telescopes across finalized checkpoints).

use alloc::string::String;

use alloy_primitives::{Address, B256, I256, U8, U64, U256};
use stylus_sdk::prelude::*;

use crate::storage::Covenant;
use crate::types::*;

// Free functions so the single #[public] impl in lib.rs stays thin;
// this module owns the logic.
/// Commits an epoch checkpoint. Epochs are strictly sequential; the ending
/// equity is in USDG base units and may be negative. `trades_root` is the
/// sorted-pair Merkle root over the epoch's canonical receipt hashes.
pub(crate) fn commit(
    c: &mut Covenant,
    strategy_id: U256,
    epoch_index: U64,
    equity: I256,
    net_flow: I256,
    trades_root: B256,
    evidence_uri: String,
) -> Result<(), CovenantError> {
    if c.paused.get() {
        return Err(CovenantError::ContractPaused(ContractPaused {}));
    }

    // Read phase.
    let (owner, status, epoch_count) = {
        let s = c.strategies.getter(strategy_id);
        (
            s.owner.get(),
            s.status.get().to::<u8>(),
            s.epoch_count.get(),
        )
    };
    if owner.is_zero() {
        return Err(CovenantError::StrategyNotFound(StrategyNotFound {}));
    }
    if c.vm().msg_sender() != owner {
        return Err(CovenantError::NotStrategyOwner(NotStrategyOwner {}));
    }
    if status != STATUS_ACTIVE {
        return Err(CovenantError::StrategySuspended(StrategySuspended {}));
    }
    if epoch_index != epoch_count {
        return Err(CovenantError::EpochNotSequential(EpochNotSequential {}));
    }
    let now = U64::from(c.vm().block_timestamp());
    if epoch_index > U64::ZERO {
        let prev_committed_at = c
            .checkpoints
            .getter(strategy_id)
            .get(epoch_index - U64::from(1))
            .committed_at
            .get();
        if prev_committed_at >= now {
            return Err(CovenantError::EpochNotSequential(EpochNotSequential {}));
        }
    }

    // Write phase.
    {
        let mut s = c.strategies.setter(strategy_id);
        s.epoch_count.set(epoch_index + U64::from(1));
    }
    {
        let mut epoch_map = c.checkpoints.setter(strategy_id);
        let mut checkpoint = epoch_map.setter(epoch_index);
        checkpoint.equity.set(equity);
        checkpoint.net_flow.set(net_flow);
        checkpoint.trades_root.set(trades_root);
        checkpoint.evidence_uri.set_str(&evidence_uri);
        checkpoint.status.set(U8::from(CP_PENDING));
        checkpoint.committed_at.set(now);
        checkpoint.challenger.set(Address::ZERO);
        checkpoint.stake.set(U256::ZERO);
    }

    c.vm().log(EpochCommitted {
        strategy_id,
        epoch_index: epoch_index.as_limbs()[0],
        equity,
        net_flow,
        trades_root,
        evidence_uri,
    });
    Ok(())
}

/// Permissionless: finalizes a pending checkpoint whose challenge window
/// has elapsed, applying its performance accounting.
pub(crate) fn finalize(
    c: &mut Covenant,
    strategy_id: U256,
    epoch_index: U64,
) -> Result<(), CovenantError> {
    if c.strategies.getter(strategy_id).owner.get().is_zero() {
        return Err(CovenantError::StrategyNotFound(StrategyNotFound {}));
    }

    // Read phase.
    let (status, committed_at, equity, net_flow) = {
        let epoch_map = c.checkpoints.getter(strategy_id);
        let cp = epoch_map.get(epoch_index);
        (
            cp.status.get().to::<u8>(),
            cp.committed_at.get(),
            cp.equity.get(),
            cp.net_flow.get(),
        )
    };
    if status != CP_PENDING {
        return Err(CovenantError::EpochNotPending(EpochNotPending {}));
    }
    let now = U64::from(c.vm().block_timestamp());
    if now < committed_at.saturating_add(c.challenge_window.get()) {
        return Err(CovenantError::ChallengeWindowActive(
            ChallengeWindowActive {},
        ));
    }

    // Write phase: checkpoint finalized, performance accounting applied.
    {
        let mut epoch_map = c.checkpoints.setter(strategy_id);
        let mut checkpoint = epoch_map.setter(epoch_index);
        checkpoint.status.set(U8::from(CP_FINALIZED));
    }
    let (prev_equity, cumulative_pnl, hwm, finalized_epochs) = {
        let s = c.strategies.getter(strategy_id);
        (
            s.equity.get(),
            s.cumulative_pnl.get(),
            s.high_water_mark.get(),
            s.finalized_epochs.get(),
        )
    };
    let pnl = equity - prev_equity - net_flow;
    {
        let mut s = c.strategies.setter(strategy_id);
        s.equity.set(equity);
        s.cumulative_pnl.set(cumulative_pnl + pnl);
        if equity > hwm {
            s.high_water_mark.set(equity);
        }
        s.finalized_epochs.set(finalized_epochs + U64::from(1));
    }

    c.vm().log(EpochFinalized {
        strategy_id,
        epoch_index: epoch_index.as_limbs()[0],
        pnl,
        equity,
    });
    Ok(())
}
