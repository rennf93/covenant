//! Covenant: onchain proof-of-performance for trading strategies.
//!
//! Strategies commit sequential epoch checkpoints (ending equity in USDG terms,
//! net external flows, and a Merkle root of canonical trade receipts). Anyone
//! may challenge a pending epoch by staking USDG; the resolver either upholds
//! (epoch invalidated, operator bond slashed) or dismisses (stake forfeited).
//! Performance accounting is applied only to finalized checkpoints.
//!
//! Receipt canonicalization and Merkle semantics live in `merkle-core` and are
//! fixture-tested against the TypeScript SDK (see contracts/merkle-core/tests).
//!
//! Module map: the ABI lives HERE as one thin `#[public]` impl (stylus-sdk
//! generates one Router per crate, so the public impl must be single), while
//! each module owns the logic for its concern as free functions:
//! - [`types`]: ABI surface types (events, IUSDG, errors, lifecycle constants)
//! - [`storage`]: the sol_storage! layout (consensus-critical field order)
//! - [`internal`]: guards + USDG transfer helpers (not part of the ABI)
//! - [`admin`]: constructor, parameters, pause, treasury, admin handover
//! - [`registry`]: registration + bond escrow
//! - [`epochs`]: commit + finalize (performance accounting)
//! - [`challenge`]: stake, resolve, resolver deadline, slash economics
//! - [`verify`]: onchain Merkle receipt verification
//! - [`views`]: read-only state access

#![cfg_attr(not(any(test, feature = "export-abi")), no_main)]
extern crate alloc;

pub mod admin;
pub mod challenge;
pub mod epochs;
pub mod internal;
pub mod registry;
pub mod storage;
pub mod types;
pub mod verify;
pub mod views;

pub use storage::Covenant;
pub use types::*;

use alloc::string::String;

use alloy_primitives::{Address, B256, I256, U8, U64, U256};
use stylus_sdk::prelude::*;

/// The public ABI. Each method validates nothing itself: validation, storage
/// access, and events live in the concern module this delegates to.
#[public]
#[allow(clippy::type_complexity)] // ABI return shapes are positional tuples by design
impl Covenant {
    /// Deploy-time initialization. `usdg` is the settlement token address.
    #[constructor]
    pub fn constructor(
        &mut self,
        usdg: Address,
        resolver: Address,
        bond_amount: U256,
        challenge_stake: U256,
        challenge_window: U64,
    ) -> Result<(), CovenantError> {
        admin::constructor(
            self,
            usdg,
            resolver,
            bond_amount,
            challenge_stake,
            challenge_window,
        )
    }

    pub fn set_resolver(&mut self, new_resolver: Address) -> Result<(), CovenantError> {
        admin::set_resolver(self, new_resolver)
    }

    pub fn set_parameters(
        &mut self,
        bond_amount: U256,
        challenge_stake: U256,
        challenge_window: U64,
    ) -> Result<(), CovenantError> {
        admin::set_parameters(self, bond_amount, challenge_stake, challenge_window)
    }

    pub fn pause(&mut self) -> Result<(), CovenantError> {
        admin::pause(self)
    }

    pub fn unpause(&mut self) -> Result<(), CovenantError> {
        admin::unpause(self)
    }

    pub fn withdraw_treasury(&mut self, to: Address, amount: U256) -> Result<(), CovenantError> {
        admin::withdraw_treasury(self, to, amount)
    }

    /// Two-step admin handover, step 1: propose a successor. Only the
    /// proposed successor can accept (see [`Covenant::accept_admin`]);
    /// proposing the zero address clears a pending proposal.
    pub fn transfer_admin(&mut self, new_admin: Address) -> Result<(), CovenantError> {
        admin::transfer_admin(self, new_admin)
    }

    /// Two-step admin handover, step 2: the proposed successor accepts and
    /// becomes the admin. Reverts for anyone else.
    pub fn accept_admin(&mut self) -> Result<(), CovenantError> {
        admin::accept_admin(self)
    }

    /// The currently proposed admin, zero when no handover is pending.
    pub fn pending_admin(&self) -> Address {
        self.pending_admin.get()
    }

    /// Registers a strategy and escrows the performance bond from the caller.
    pub fn register_strategy(
        &mut self,
        name: String,
        metadata_uri: String,
    ) -> Result<U256, CovenantError> {
        registry::register_strategy(self, name, metadata_uri)
    }

    /// Commits an epoch checkpoint. Epochs are strictly sequential and the
    /// previous checkpoint must be Finalized first, so a pending or
    /// challenged epoch blocks later commits until it resolves. The ending
    /// equity is in USDG base units and may be negative.
    pub fn commit_epoch(
        &mut self,
        strategy_id: U256,
        epoch_index: U64,
        equity: I256,
        net_flow: I256,
        trades_root: B256,
        evidence_uri: String,
    ) -> Result<(), CovenantError> {
        epochs::commit(
            self,
            strategy_id,
            epoch_index,
            equity,
            net_flow,
            trades_root,
            evidence_uri,
        )
    }

    /// Permissionless (and pause-exempt: it settles accounting, moves no
    /// tokens): finalizes a pending checkpoint whose challenge window has
    /// elapsed, applying its performance accounting. Epochs finalize in
    /// strict order.
    pub fn finalize_epoch(
        &mut self,
        strategy_id: U256,
        epoch_index: U64,
    ) -> Result<(), CovenantError> {
        epochs::finalize(self, strategy_id, epoch_index)
    }

    /// Challenges a pending epoch by staking USDG. Only one challenger at a
    /// time; dismissal closes the epoch, an upheld challenge ends the
    /// strategy. Gated by pause.
    pub fn challenge_epoch(
        &mut self,
        strategy_id: U256,
        epoch_index: U64,
        reason: String,
    ) -> Result<(), CovenantError> {
        challenge::challenge(self, strategy_id, epoch_index, reason)
    }

    /// Resolution: the resolver may decide at any time; once the challenge is
    /// at least three challenge windows old, anyone may resolve and the
    /// decision is forced-dismiss with the stake refunded (the event carries
    /// `forced = true`). Gated by pause.
    pub fn resolve_challenge(
        &mut self,
        strategy_id: U256,
        epoch_index: U64,
        upheld: bool,
    ) -> Result<(), CovenantError> {
        challenge::resolve(self, strategy_id, epoch_index, upheld)
    }

    /// Verifies a canonical receipt hash against a committed epoch root.
    /// Returns false for unknown epochs and zero roots (empty epochs).
    pub fn verify_receipt(
        &self,
        strategy_id: U256,
        epoch_index: U64,
        proof: Vec<B256>,
        receipt: B256,
    ) -> bool {
        verify::verify_receipt(self, strategy_id, epoch_index, proof, receipt)
    }

    pub fn strategy_count(&self) -> U256 {
        views::strategy_count(self)
    }

    pub fn config(&self) -> (Address, Address, Address, U256, U256, U64, bool, U256) {
        views::config(self)
    }

    pub fn get_strategy(
        &self,
        strategy_id: U256,
    ) -> Result<(Address, String, String, U256, U8, U64), CovenantError> {
        views::get_strategy(self, strategy_id)
    }

    pub fn get_performance(
        &self,
        strategy_id: U256,
    ) -> Result<(I256, I256, I256, U64, U64), CovenantError> {
        views::get_performance(self, strategy_id)
    }

    pub fn get_checkpoint(
        &self,
        strategy_id: U256,
        epoch_index: U64,
    ) -> Result<(I256, I256, B256, String, U8, U64, Address, U256), CovenantError> {
        views::get_checkpoint(self, strategy_id, epoch_index)
    }
}

#[cfg(test)]
mod test;
