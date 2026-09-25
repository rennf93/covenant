//! Strategy registration: the USDG performance bond escrow and id assignment.

use alloc::string::String;

use alloy_primitives::{U8, U64, U256};
use stylus_sdk::prelude::*;

use crate::storage::Covenant;
use crate::types::*;

// Free functions so the single #[public] impl in lib.rs stays thin;
// this module owns the logic.
/// Registers a strategy and escrows the performance bond from the caller.
pub(crate) fn register_strategy(
    c: &mut Covenant,
    name: String,
    metadata_uri: String,
) -> Result<U256, CovenantError> {
    if c.paused.get() {
        return Err(CovenantError::ContractPaused(ContractPaused {}));
    }
    let sender = c.vm().msg_sender();
    let bond = c.bond_amount.get();
    let id = c.strategy_count.get() + U256::from(1);
    let now = c.vm().block_timestamp();

    // Effects before the token interaction (checks, effects, interactions).
    c.strategy_count.set(id);
    {
        let mut strategy = c.strategies.setter(id);
        strategy.owner.set(sender);
        strategy.name.set_str(&name);
        strategy.metadata_uri.set_str(&metadata_uri);
        strategy.bond.set(bond);
        strategy.status.set(U8::from(STATUS_ACTIVE));
        strategy.created_at.set(U64::from(now));
    }
    c.usdg_transfer_from(sender, c.vm().contract_address(), bond)?;

    c.vm().log(StrategyRegistered {
        strategy_id: id,
        owner: sender,
        name,
        bond,
    });
    Ok(id)
}
