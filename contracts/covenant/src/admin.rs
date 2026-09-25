//! Admin surface: deploy-time construction, parameter governance, pause,
//! treasury withdrawal, and the two-step admin handover. All admin checks
//! route through [`Covenant::only_admin`], and every state-changing admin
//! action emits an event.

use alloy_primitives::{Address, U64, U256};
use stylus_sdk::prelude::*;

use crate::storage::Covenant;
use crate::types::*;

// Free functions so the single #[public] impl in lib.rs stays thin;
// this module owns the logic.
/// Deploy-time initialization. `usdg` is the settlement token address.
pub(crate) fn constructor(
    c: &mut Covenant,
    usdg: Address,
    resolver: Address,
    bond_amount: U256,
    challenge_stake: U256,
    challenge_window: U64,
) -> Result<(), CovenantError> {
    if usdg.is_zero() || resolver.is_zero() {
        return Err(CovenantError::ZeroAddress(ZeroAddress {}));
    }
    if bond_amount == U256::ZERO || challenge_stake == U256::ZERO || challenge_window.is_zero() {
        return Err(CovenantError::ZeroAmount(ZeroAmount {}));
    }
    let sender = c.vm().msg_sender();
    c.admin.set(sender);
    c.usdg.set(usdg);
    c.resolver.set(resolver);
    c.bond_amount.set(bond_amount);
    c.challenge_stake.set(challenge_stake);
    c.challenge_window.set(challenge_window);
    c.paused.set(false);
    c.strategy_count.set(U256::ZERO);
    c.treasury.set(U256::ZERO);
    c.pending_admin.set(Address::ZERO);
    Ok(())
}

// Admin -------------------------------------------------------------

pub(crate) fn set_resolver(c: &mut Covenant, new_resolver: Address) -> Result<(), CovenantError> {
    c.only_admin()?;
    let old_resolver = c.resolver.get();
    c.resolver.set(new_resolver);
    c.vm().log(ResolverSet {
        old_resolver,
        new_resolver,
    });
    Ok(())
}

pub(crate) fn set_parameters(
    c: &mut Covenant,
    bond_amount: U256,
    challenge_stake: U256,
    challenge_window: U64,
) -> Result<(), CovenantError> {
    c.only_admin()?;
    if bond_amount == U256::ZERO || challenge_stake == U256::ZERO || challenge_window.is_zero() {
        return Err(CovenantError::ZeroAmount(ZeroAmount {}));
    }
    c.bond_amount.set(bond_amount);
    c.challenge_stake.set(challenge_stake);
    c.challenge_window.set(challenge_window);
    c.vm().log(ParametersSet {
        bond_amount,
        challenge_stake,
        challenge_window: challenge_window.as_limbs()[0],
    });
    Ok(())
}

pub(crate) fn pause(c: &mut Covenant) -> Result<(), CovenantError> {
    c.only_admin()?;
    c.paused.set(true);
    c.vm().log(Paused {
        account: c.vm().msg_sender(),
    });
    Ok(())
}

pub(crate) fn unpause(c: &mut Covenant) -> Result<(), CovenantError> {
    c.only_admin()?;
    c.paused.set(false);
    c.vm().log(Unpaused {
        account: c.vm().msg_sender(),
    });
    Ok(())
}

pub(crate) fn withdraw_treasury(
    c: &mut Covenant,
    to: Address,
    amount: U256,
) -> Result<(), CovenantError> {
    c.only_admin()?;
    if to.is_zero() {
        return Err(CovenantError::ZeroAddress(ZeroAddress {}));
    }
    let treasury = c.treasury.get();
    if amount > treasury {
        return Err(CovenantError::TreasuryOverdraw(TreasuryOverdraw {}));
    }
    // Effects first, interaction last.
    c.treasury.set(treasury - amount);
    c.usdg_transfer(to, amount)?;
    c.vm().log(TreasuryWithdrawn { to, amount });
    Ok(())
}

// Two-step admin handover -------------------------------------------

/// Step 1: the current admin proposes a successor. Proposing the zero
/// address clears any pending proposal (a cancellation), so the proposal
/// can never be accepted by an uncontrolled account.
pub(crate) fn transfer_admin(c: &mut Covenant, new_admin: Address) -> Result<(), CovenantError> {
    c.only_admin()?;
    c.pending_admin.set(new_admin);
    c.vm().log(AdminTransferProposed {
        current_admin: c.vm().msg_sender(),
        new_admin,
    });
    Ok(())
}

/// Step 2: only the proposed successor can accept. Until acceptance the
/// current admin keeps full authority and can re-propose at will.
pub(crate) fn accept_admin(c: &mut Covenant) -> Result<(), CovenantError> {
    let sender = c.vm().msg_sender();
    if sender != c.pending_admin.get() {
        return Err(CovenantError::NotPendingAdmin(NotPendingAdmin {}));
    }
    c.admin.set(sender);
    c.pending_admin.set(Address::ZERO);
    c.vm().log(AdminTransferAccepted { new_admin: sender });
    Ok(())
}
