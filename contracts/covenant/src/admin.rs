//! Admin surface: deploy-time construction, parameter governance, pause, and
//! treasury withdrawal. All resolver/admin checks route through
//! [`Covenant::only_admin`].

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
    Ok(())
}

// Admin -------------------------------------------------------------

pub(crate) fn set_resolver(c: &mut Covenant, new_resolver: Address) -> Result<(), CovenantError> {
    c.only_admin()?;
    c.resolver.set(new_resolver);
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
    Ok(())
}

pub(crate) fn pause(c: &mut Covenant) -> Result<(), CovenantError> {
    c.only_admin()?;
    c.paused.set(true);
    Ok(())
}

pub(crate) fn unpause(c: &mut Covenant) -> Result<(), CovenantError> {
    c.only_admin()?;
    c.paused.set(false);
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
        return Err(CovenantError::ZeroAmount(ZeroAmount {}));
    }
    c.treasury.set(treasury - amount);
    c.usdg_transfer(to, amount)
}
