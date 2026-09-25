//! Internal helpers shared by the function modules: guards and the USDG
//! token interactions. Not part of the ABI (no `#[public]` impl).

use alloy_primitives::{Address, U256};
use stylus_sdk::prelude::*;

use crate::storage::Covenant;
use crate::types::{CovenantError, IUSDG, NotAdmin, TokenTransferFailed};

impl Covenant {
    pub(crate) fn only_admin(&self) -> Result<(), CovenantError> {
        if self.vm().msg_sender() != self.admin.get() {
            return Err(CovenantError::NotAdmin(NotAdmin {}));
        }
        Ok(())
    }

    pub(crate) fn usdg_transfer_from(
        &mut self,
        from: Address,
        to: Address,
        amount: U256,
    ) -> Result<(), CovenantError> {
        let usdg = IUSDG::new(self.usdg.get());
        let context = Call::new_mutating(self);
        let ok = usdg
            .transfer_from(self.vm(), context, from, to, amount)
            .map_err(|_| CovenantError::TokenTransferFailed(TokenTransferFailed {}))?;
        if ok {
            Ok(())
        } else {
            Err(CovenantError::TokenTransferFailed(TokenTransferFailed {}))
        }
    }

    pub(crate) fn usdg_transfer(&mut self, to: Address, amount: U256) -> Result<(), CovenantError> {
        let usdg = IUSDG::new(self.usdg.get());
        let context = Call::new_mutating(self);
        let ok = usdg
            .transfer(self.vm(), context, to, amount)
            .map_err(|_| CovenantError::TokenTransferFailed(TokenTransferFailed {}))?;
        if ok {
            Ok(())
        } else {
            Err(CovenantError::TokenTransferFailed(TokenTransferFailed {}))
        }
    }
}
