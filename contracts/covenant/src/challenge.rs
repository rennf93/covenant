//! The optimistic challenge game: stake to challenge a pending epoch, then
//! the resolver upholds (fraud: slash the operator bond) or dismisses
//! (stake forfeited to the treasury).

use alloc::string::String;

use alloy_primitives::{Address, U8, U64, U256};
use stylus_sdk::prelude::*;

use crate::storage::Covenant;
use crate::types::*;

// Free functions so the single #[public] impl in lib.rs stays thin;
// this module owns the logic.
/// Challenges a pending epoch by staking USDG. Only one challenger at a
/// time; dismissal closes the epoch, an upheld challenge ends the strategy.
pub(crate) fn challenge(
    c: &mut Covenant,
    strategy_id: U256,
    epoch_index: U64,
    reason: String,
) -> Result<(), CovenantError> {
    // Read phase.
    let (status, committed_at) = {
        let epoch_map = c.checkpoints.getter(strategy_id);
        let cp = epoch_map.get(epoch_index);
        (cp.status.get().to::<u8>(), cp.committed_at.get())
    };
    if status != CP_PENDING {
        return Err(CovenantError::EpochNotPending(EpochNotPending {}));
    }
    let now = U64::from(c.vm().block_timestamp());
    if now >= committed_at.saturating_add(c.challenge_window.get()) {
        return Err(CovenantError::ChallengeWindowElapsed(
            ChallengeWindowElapsed {},
        ));
    }

    let sender = c.vm().msg_sender();
    let stake = c.challenge_stake.get();
    c.usdg_transfer_from(sender, c.vm().contract_address(), stake)?;

    {
        let mut epoch_map = c.checkpoints.setter(strategy_id);
        let mut checkpoint = epoch_map.setter(epoch_index);
        checkpoint.status.set(U8::from(CP_CHALLENGED));
        checkpoint.challenger.set(sender);
        checkpoint.stake.set(stake);
    }

    c.vm().log(EpochChallenged {
        strategy_id,
        epoch_index: epoch_index.as_limbs()[0],
        challenger: sender,
        reason,
    });
    Ok(())
}

/// Resolver decision. Upheld: stake refunded to challenger, operator bond
/// slashed to the challenger, strategy suspended, checkpoint invalidated.
/// Dismissed: stake forfeited to the treasury, checkpoint finalized.
pub(crate) fn resolve(
    c: &mut Covenant,
    strategy_id: U256,
    epoch_index: U64,
    upheld: bool,
) -> Result<(), CovenantError> {
    if c.vm().msg_sender() != c.resolver.get() {
        return Err(CovenantError::NotResolver(NotResolver {}));
    }

    // Read phase.
    let (status, challenger, stake) = {
        let epoch_map = c.checkpoints.getter(strategy_id);
        let cp = epoch_map.get(epoch_index);
        (
            cp.status.get().to::<u8>(),
            cp.challenger.get(),
            cp.stake.get(),
        )
    };
    if status != CP_CHALLENGED {
        return Err(CovenantError::AlreadyChallenged(AlreadyChallenged {}));
    }
    let resolver = c.vm().msg_sender();

    if upheld {
        {
            let mut epoch_map = c.checkpoints.setter(strategy_id);
            let mut checkpoint = epoch_map.setter(epoch_index);
            checkpoint.status.set(U8::from(CP_INVALIDATED));
            checkpoint.stake.set(U256::ZERO);
            checkpoint.challenger.set(Address::ZERO);
        }
        let bond = c.strategies.getter(strategy_id).bond.get();
        {
            let mut s = c.strategies.setter(strategy_id);
            s.bond.set(U256::ZERO);
            s.status.set(U8::from(STATUS_SUSPENDED));
        }

        // Interactions last: refund the stake, then hand over the bond.
        c.usdg_transfer(challenger, stake)?;
        if bond > U256::ZERO {
            c.usdg_transfer(challenger, bond)?;
        }

        c.vm().log(StrategyStatusChanged {
            strategy_id,
            status: STATUS_SUSPENDED,
        });
    } else {
        {
            let mut epoch_map = c.checkpoints.setter(strategy_id);
            let mut checkpoint = epoch_map.setter(epoch_index);
            checkpoint.status.set(U8::from(CP_FINALIZED));
            checkpoint.stake.set(U256::ZERO);
            checkpoint.challenger.set(Address::ZERO);
        }
        // Forfeited stake stays in the contract; tracked for withdrawal.
        c.treasury.set(c.treasury.get() + stake);
    }

    c.vm().log(ChallengeResolved {
        strategy_id,
        epoch_index: epoch_index.as_limbs()[0],
        upheld,
        resolver,
    });
    Ok(())
}
