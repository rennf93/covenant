//! The optimistic challenge game: stake to challenge a pending epoch, then
//! the resolver upholds (fraud: slash the operator bond) or dismisses
//! (stake forfeited to the treasury). If the resolver never acts, resolution
//! becomes permissionless after the deadline and force-dismisses with the
//! stake refunded, so a missing or captured resolver cannot hold epochs or
//! challenger funds hostage.

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
    if c.paused.get() {
        return Err(CovenantError::ContractPaused(ContractPaused {}));
    }

    // Existence: unknown strategies and uncommitted epochs must not be
    // challengeable. (A phantom epoch reads zero-initialized storage as a
    // Pending checkpoint at time 0, which the window check alone does not
    // catch inside the first window.)
    let (owner, epoch_count) = {
        let s = c.strategies.getter(strategy_id);
        (s.owner.get(), s.epoch_count.get())
    };
    if owner.is_zero() {
        return Err(CovenantError::StrategyNotFound(StrategyNotFound {}));
    }
    if epoch_index >= epoch_count {
        return Err(CovenantError::EpochUnknown(EpochUnknown {}));
    }

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

    // Effects before the token interaction (checks, effects, interactions).
    {
        let mut epoch_map = c.checkpoints.setter(strategy_id);
        let mut checkpoint = epoch_map.setter(epoch_index);
        checkpoint.status.set(U8::from(CP_CHALLENGED));
        checkpoint.challenger.set(sender);
        checkpoint.stake.set(stake);
    }
    c.usdg_transfer_from(sender, c.vm().contract_address(), stake)?;

    c.vm().log(EpochChallenged {
        strategy_id,
        epoch_index: epoch_index.as_limbs()[0],
        challenger: sender,
        reason,
    });
    Ok(())
}

/// Resolution. The resolver may decide at any time: upheld slashes the
/// operator bond to the challenger, dismissed forfeits the stake to the
/// treasury.
///
/// Resolver deadline (anti-deadlock): an epoch can only be challenged within
/// one `challenge_window` of its commit, so once `now >= committed_at +
/// 4 * challenge_window` the challenge is at least three windows old. From
/// then on anyone may resolve, and the decision is forced-dismiss: the stake
/// is refunded to the challenger, the checkpoint finalizes, and nothing goes
/// to the treasury (nobody actually judged the challenge spurious). The
/// emitted `ChallengeResolved` carries `forced = true`. The deadline derives
/// from the challenge window parameter as it stands at resolve time.
pub(crate) fn resolve(
    c: &mut Covenant,
    strategy_id: U256,
    epoch_index: U64,
    upheld: bool,
) -> Result<(), CovenantError> {
    if c.paused.get() {
        return Err(CovenantError::ContractPaused(ContractPaused {}));
    }

    // Read phase. A non-challenged checkpoint (including a phantom epoch,
    // whose zero-initialized status reads as Pending) has nothing to resolve.
    let (status, committed_at, challenger, stake) = {
        let epoch_map = c.checkpoints.getter(strategy_id);
        let cp = epoch_map.get(epoch_index);
        (
            cp.status.get().to::<u8>(),
            cp.committed_at.get(),
            cp.challenger.get(),
            cp.stake.get(),
        )
    };
    if status != CP_CHALLENGED {
        return Err(CovenantError::NotChallenged(NotChallenged {}));
    }

    let sender = c.vm().msg_sender();
    let is_resolver = sender == c.resolver.get();
    let deadline =
        committed_at.saturating_add(c.challenge_window.get().saturating_mul(U64::from(4)));
    let forced = !is_resolver && U64::from(c.vm().block_timestamp()) >= deadline;
    if !is_resolver && !forced {
        return Err(CovenantError::NotResolver(NotResolver {}));
    }

    // A forced resolution is always a dismissal: the `upheld` argument is
    // only meaningful when the resolver (or the pre-deadline window) decides.
    let upheld = upheld && !forced;

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
        if forced {
            // Forced dismissal refunds: no resolver ever judged the challenge.
            c.usdg_transfer(challenger, stake)?;
        } else {
            // Forfeited stake stays in the contract; tracked for withdrawal.
            c.treasury.set(c.treasury.get() + stake);
        }
    }

    c.vm().log(ChallengeResolved {
        strategy_id,
        epoch_index: epoch_index.as_limbs()[0],
        upheld,
        forced,
        resolver: sender,
    });
    Ok(())
}
