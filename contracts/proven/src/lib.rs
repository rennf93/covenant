//! Proven: onchain proof-of-performance for trading strategies.
//!
//! Strategies commit sequential epoch checkpoints (ending equity in USDG terms,
//! net external flows, and a Merkle root of canonical trade receipts). Anyone
//! may challenge a pending epoch by staking USDG; the resolver either upholds
//! (epoch invalidated, operator bond slashed) or dismisses (stake forfeited).
//! Performance accounting is applied only to finalized checkpoints.
//!
//! Receipt canonicalization and Merkle semantics live in `merkle-core` and are
//! fixture-tested against the TypeScript SDK (see contracts/merkle-core/tests).

#![cfg_attr(not(any(test, feature = "export-abi")), no_main)]
extern crate alloc;

use alloc::{string::String, vec::Vec};

use alloy_primitives::{Address, B256, I256, U256, U64, U8};
use alloy_sol_types::sol;
use stylus_sdk::prelude::*;

use merkle_core::verify_proof;

sol! {
    event StrategyRegistered(uint256 indexed strategy_id, address indexed owner, string name, uint256 bond);
    event EpochCommitted(uint256 indexed strategy_id, uint64 indexed epoch_index, int256 equity, int256 net_flow, bytes32 trades_root, string evidence_uri);
    event EpochFinalized(uint256 indexed strategy_id, uint64 indexed epoch_index, int256 pnl, int256 equity);
    event EpochChallenged(uint256 indexed strategy_id, uint64 indexed epoch_index, address indexed challenger, string reason);
    event ChallengeResolved(uint256 indexed strategy_id, uint64 indexed epoch_index, bool upheld, address indexed resolver);
    event StrategyStatusChanged(uint256 indexed strategy_id, uint8 status);
}

sol_interface! {
    /// Minimal ERC-20 surface used for USDG bonds, stakes, and payouts.
    interface IUSDG {
        function transferFrom(address from, address to, uint256 amount) external returns (bool);
        function transfer(address to, uint256 amount) external returns (bool);
    }
}

sol! {
    error NotAdmin();
    error NotResolver();
    error NotStrategyOwner();
    error ContractPaused();
    error StrategyNotFound();
    error StrategySuspended();
    error EpochNotSequential();
    error EpochUnknown();
    error EpochNotPending();
    error ChallengeWindowElapsed();
    error ChallengeWindowActive();
    error AlreadyChallenged();
    error ZeroAddress();
    error ZeroAmount();
    error TokenTransferFailed();
}

#[derive(SolidityError)]
pub enum ProvenError {
    NotAdmin(NotAdmin),
    NotResolver(NotResolver),
    NotStrategyOwner(NotStrategyOwner),
    ContractPaused(ContractPaused),
    StrategyNotFound(StrategyNotFound),
    StrategySuspended(StrategySuspended),
    EpochNotSequential(EpochNotSequential),
    EpochUnknown(EpochUnknown),
    EpochNotPending(EpochNotPending),
    ChallengeWindowElapsed(ChallengeWindowElapsed),
    ChallengeWindowActive(ChallengeWindowActive),
    AlreadyChallenged(AlreadyChallenged),
    ZeroAddress(ZeroAddress),
    ZeroAmount(ZeroAmount),
    TokenTransferFailed(TokenTransferFailed),
}

impl core::fmt::Debug for ProvenError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        let name = match self {
            ProvenError::NotAdmin(_) => "NotAdmin",
            ProvenError::NotResolver(_) => "NotResolver",
            ProvenError::NotStrategyOwner(_) => "NotStrategyOwner",
            ProvenError::ContractPaused(_) => "ContractPaused",
            ProvenError::StrategyNotFound(_) => "StrategyNotFound",
            ProvenError::StrategySuspended(_) => "StrategySuspended",
            ProvenError::EpochNotSequential(_) => "EpochNotSequential",
            ProvenError::EpochUnknown(_) => "EpochUnknown",
            ProvenError::EpochNotPending(_) => "EpochNotPending",
            ProvenError::ChallengeWindowElapsed(_) => "ChallengeWindowElapsed",
            ProvenError::ChallengeWindowActive(_) => "ChallengeWindowActive",
            ProvenError::AlreadyChallenged(_) => "AlreadyChallenged",
            ProvenError::ZeroAddress(_) => "ZeroAddress",
            ProvenError::ZeroAmount(_) => "ZeroAmount",
            ProvenError::TokenTransferFailed(_) => "TokenTransferFailed",
        };
        f.write_str(name)
    }
}

/// Strategy lifecycle status.
pub const STATUS_ACTIVE: u8 = 0;
pub const STATUS_SUSPENDED: u8 = 1;

/// Checkpoint lifecycle status.
pub const CP_PENDING: u8 = 0;
pub const CP_FINALIZED: u8 = 1;
pub const CP_CHALLENGED: u8 = 2;
pub const CP_INVALIDATED: u8 = 3;

// Storage ---------------------------------------------------------------

sol_storage! {
    #[entrypoint]
    pub struct Proven {
        address admin;
        address resolver;
        address usdg;
        bool paused;
        uint256 bond_amount;
        uint256 challenge_stake;
        uint64 challenge_window;
        uint256 treasury;
        uint256 strategy_count;
        mapping(uint256 => Strategy) strategies;
        mapping(uint256 => mapping(uint64 => Checkpoint)) checkpoints;
    }
}

sol_storage! {
    pub struct Strategy {
        address owner;
        string name;
        string metadata_uri;
        uint256 bond;
        uint8 status;
        uint64 created_at;
        uint64 epoch_count;
        uint64 finalized_epochs;
        int256 equity;
        int256 high_water_mark;
        int256 cumulative_pnl;
    }
}

sol_storage! {
    pub struct Checkpoint {
        int256 equity;
        int256 net_flow;
        bytes32 trades_root;
        string evidence_uri;
        uint8 status;
        uint64 committed_at;
        address challenger;
        uint256 stake;
    }
}

// Contract ---------------------------------------------------------------

#[public]
impl Proven {
    /// Deploy-time initialization. `usdg` is the settlement token address.
    #[constructor]
    pub fn constructor(
        &mut self,
        usdg: Address,
        resolver: Address,
        bond_amount: U256,
        challenge_stake: U256,
        challenge_window: U64,
    ) -> Result<(), ProvenError> {
        if usdg.is_zero() || resolver.is_zero() {
            return Err(ProvenError::ZeroAddress(ZeroAddress {}));
        }
        if bond_amount == U256::ZERO || challenge_stake == U256::ZERO || challenge_window.is_zero() {
            return Err(ProvenError::ZeroAmount(ZeroAmount {}));
        }
        let sender = self.vm().msg_sender();
        self.admin.set(sender);
        self.usdg.set(usdg);
        self.resolver.set(resolver);
        self.bond_amount.set(bond_amount);
        self.challenge_stake.set(challenge_stake);
        self.challenge_window.set(challenge_window);
        self.paused.set(false);
        self.strategy_count.set(U256::ZERO);
        self.treasury.set(U256::ZERO);
        Ok(())
    }

    // Admin -------------------------------------------------------------

    pub fn set_resolver(&mut self, new_resolver: Address) -> Result<(), ProvenError> {
        self.only_admin()?;
        self.resolver.set(new_resolver);
        Ok(())
    }

    pub fn set_parameters(
        &mut self,
        bond_amount: U256,
        challenge_stake: U256,
        challenge_window: U64,
    ) -> Result<(), ProvenError> {
        self.only_admin()?;
        if bond_amount == U256::ZERO || challenge_stake == U256::ZERO || challenge_window.is_zero() {
            return Err(ProvenError::ZeroAmount(ZeroAmount {}));
        }
        self.bond_amount.set(bond_amount);
        self.challenge_stake.set(challenge_stake);
        self.challenge_window.set(challenge_window);
        Ok(())
    }

    pub fn pause(&mut self) -> Result<(), ProvenError> {
        self.only_admin()?;
        self.paused.set(true);
        Ok(())
    }

    pub fn unpause(&mut self) -> Result<(), ProvenError> {
        self.only_admin()?;
        self.paused.set(false);
        Ok(())
    }

    pub fn withdraw_treasury(&mut self, to: Address, amount: U256) -> Result<(), ProvenError> {
        self.only_admin()?;
        if to.is_zero() {
            return Err(ProvenError::ZeroAddress(ZeroAddress {}));
        }
        let treasury = self.treasury.get();
        if amount > treasury {
            return Err(ProvenError::ZeroAmount(ZeroAmount {}));
        }
        self.treasury.set(treasury - amount);
        self.usdg_transfer(to, amount)
    }

    // Strategy lifecycle -------------------------------------------------

    /// Registers a strategy and escrows the performance bond from the caller.
    pub fn register_strategy(
        &mut self,
        name: String,
        metadata_uri: String,
    ) -> Result<U256, ProvenError> {
        if self.paused.get() {
            return Err(ProvenError::ContractPaused(ContractPaused {}));
        }
        let sender = self.vm().msg_sender();
        let bond = self.bond_amount.get();

        self.usdg_transfer_from(sender, self.vm().contract_address(), bond)?;

        let id = self.strategy_count.get() + U256::from(1);
        self.strategy_count.set(id);
        let now = self.vm().block_timestamp();

        {
            let mut strategy = self.strategies.setter(id);
            strategy.owner.set(sender);
            strategy.name.set_str(&name);
            strategy.metadata_uri.set_str(&metadata_uri);
            strategy.bond.set(bond);
            strategy.status.set(U8::from(STATUS_ACTIVE));
            strategy.created_at.set(U64::from(now));
        }

        self.vm().log(StrategyRegistered {
            strategy_id: id,
            owner: sender,
            name,
            bond,
        });
        Ok(id)
    }

    /// Commits an epoch checkpoint. Epochs are strictly sequential; the ending
    /// equity is in USDG base units and may be negative. `trades_root` is the
    /// sorted-pair Merkle root over the epoch's canonical receipt hashes.
    pub fn commit_epoch(
        &mut self,
        strategy_id: U256,
        epoch_index: U64,
        equity: I256,
        net_flow: I256,
        trades_root: B256,
        evidence_uri: String,
    ) -> Result<(), ProvenError> {
        if self.paused.get() {
            return Err(ProvenError::ContractPaused(ContractPaused {}));
        }

        // Read phase.
        let (owner, status, epoch_count) = {
            let s = self.strategies.getter(strategy_id);
            (s.owner.get(), s.status.get().to::<u8>(), s.epoch_count.get())
        };
        if owner.is_zero() {
            return Err(ProvenError::StrategyNotFound(StrategyNotFound {}));
        }
        if self.vm().msg_sender() != owner {
            return Err(ProvenError::NotStrategyOwner(NotStrategyOwner {}));
        }
        if status != STATUS_ACTIVE {
            return Err(ProvenError::StrategySuspended(StrategySuspended {}));
        }
        if epoch_index != epoch_count {
            return Err(ProvenError::EpochNotSequential(EpochNotSequential {}));
        }
        let now = U64::from(self.vm().block_timestamp());
        if epoch_index > U64::ZERO {
            let prev_committed_at = self
                .checkpoints
                .getter(strategy_id)
                .get(epoch_index - U64::from(1))
                .committed_at
                .get();
            if prev_committed_at >= now {
                return Err(ProvenError::EpochNotSequential(EpochNotSequential {}));
            }
        }

        // Write phase.
        {
            let mut s = self.strategies.setter(strategy_id);
            s.epoch_count.set(epoch_index + U64::from(1));
        }
        {
            let mut epoch_map = self.checkpoints.setter(strategy_id);
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

        self.vm().log(EpochCommitted {
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
    pub fn finalize_epoch(&mut self, strategy_id: U256, epoch_index: U64) -> Result<(), ProvenError> {
        if self.strategies.getter(strategy_id).owner.get().is_zero() {
            return Err(ProvenError::StrategyNotFound(StrategyNotFound {}));
        }

        // Read phase.
        let (status, committed_at, equity, net_flow) = {
            let epoch_map = self.checkpoints.getter(strategy_id);
            let cp = epoch_map.get(epoch_index);
            (cp.status.get().to::<u8>(), cp.committed_at.get(), cp.equity.get(), cp.net_flow.get())
        };
        if status != CP_PENDING {
            return Err(ProvenError::EpochNotPending(EpochNotPending {}));
        }
        let now = U64::from(self.vm().block_timestamp());
        if now < committed_at.saturating_add(self.challenge_window.get()) {
            return Err(ProvenError::ChallengeWindowActive(ChallengeWindowActive {}));
        }

        // Write phase: checkpoint finalized, performance accounting applied.
        {
            let mut epoch_map = self.checkpoints.setter(strategy_id);
            let mut checkpoint = epoch_map.setter(epoch_index);
            checkpoint.status.set(U8::from(CP_FINALIZED));
        }
        let (prev_equity, cumulative_pnl, hwm, finalized_epochs) = {
            let s = self.strategies.getter(strategy_id);
            (
                s.equity.get(),
                s.cumulative_pnl.get(),
                s.high_water_mark.get(),
                s.finalized_epochs.get(),
            )
        };
        let pnl = equity - prev_equity - net_flow;
        {
            let mut s = self.strategies.setter(strategy_id);
            s.equity.set(equity);
            s.cumulative_pnl.set(cumulative_pnl + pnl);
            if equity > hwm {
                s.high_water_mark.set(equity);
            }
            s.finalized_epochs.set(finalized_epochs + U64::from(1));
        }

        self.vm().log(EpochFinalized {
            strategy_id,
            epoch_index: epoch_index.as_limbs()[0],
            pnl,
            equity,
        });
        Ok(())
    }

    /// Challenges a pending epoch by staking USDG. Only one challenger at a
    /// time; dismissal closes the epoch, an upheld challenge ends the strategy.
    pub fn challenge_epoch(
        &mut self,
        strategy_id: U256,
        epoch_index: U64,
        reason: String,
    ) -> Result<(), ProvenError> {
        // Read phase.
        let (status, committed_at) = {
            let epoch_map = self.checkpoints.getter(strategy_id);
            let cp = epoch_map.get(epoch_index);
            (cp.status.get().to::<u8>(), cp.committed_at.get())
        };
        if status != CP_PENDING {
            return Err(ProvenError::EpochNotPending(EpochNotPending {}));
        }
        let now = U64::from(self.vm().block_timestamp());
        if now >= committed_at.saturating_add(self.challenge_window.get()) {
            return Err(ProvenError::ChallengeWindowElapsed(ChallengeWindowElapsed {}));
        }

        let sender = self.vm().msg_sender();
        let stake = self.challenge_stake.get();
        self.usdg_transfer_from(sender, self.vm().contract_address(), stake)?;

        {
            let mut epoch_map = self.checkpoints.setter(strategy_id);
            let mut checkpoint = epoch_map.setter(epoch_index);
            checkpoint.status.set(U8::from(CP_CHALLENGED));
            checkpoint.challenger.set(sender);
            checkpoint.stake.set(stake);
        }

        self.vm().log(EpochChallenged {
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
    pub fn resolve_challenge(
        &mut self,
        strategy_id: U256,
        epoch_index: U64,
        upheld: bool,
    ) -> Result<(), ProvenError> {
        if self.vm().msg_sender() != self.resolver.get() {
            return Err(ProvenError::NotResolver(NotResolver {}));
        }

        // Read phase.
        let (status, challenger, stake) = {
            let epoch_map = self.checkpoints.getter(strategy_id);
            let cp = epoch_map.get(epoch_index);
            (cp.status.get().to::<u8>(), cp.challenger.get(), cp.stake.get())
        };
        if status != CP_CHALLENGED {
            return Err(ProvenError::AlreadyChallenged(AlreadyChallenged {}));
        }
        let resolver = self.vm().msg_sender();

        if upheld {
            {
                let mut epoch_map = self.checkpoints.setter(strategy_id);
                let mut checkpoint = epoch_map.setter(epoch_index);
                checkpoint.status.set(U8::from(CP_INVALIDATED));
                checkpoint.stake.set(U256::ZERO);
                checkpoint.challenger.set(Address::ZERO);
            }
            let bond = self.strategies.getter(strategy_id).bond.get();
            {
                let mut s = self.strategies.setter(strategy_id);
                s.bond.set(U256::ZERO);
                s.status.set(U8::from(STATUS_SUSPENDED));
            }

            // Interactions last: refund the stake, then hand over the bond.
            self.usdg_transfer(challenger, stake)?;
            if bond > U256::ZERO {
                self.usdg_transfer(challenger, bond)?;
            }

            self.vm().log(StrategyStatusChanged {
                strategy_id,
                status: STATUS_SUSPENDED,
            });
        } else {
            {
                let mut epoch_map = self.checkpoints.setter(strategy_id);
                let mut checkpoint = epoch_map.setter(epoch_index);
                checkpoint.status.set(U8::from(CP_FINALIZED));
                checkpoint.stake.set(U256::ZERO);
                checkpoint.challenger.set(Address::ZERO);
            }
            // Forfeited stake stays in the contract; tracked for withdrawal.
            self.treasury.set(self.treasury.get() + stake);
        }

        self.vm().log(ChallengeResolved {
            strategy_id,
            epoch_index: epoch_index.as_limbs()[0],
            upheld,
            resolver,
        });
        Ok(())
    }

    // Views ---------------------------------------------------------------

    /// Verifies a canonical receipt hash against a committed epoch root.
    /// Returns false for unknown epochs and zero roots (empty epochs).
    pub fn verify_receipt(
        &self,
        strategy_id: U256,
        epoch_index: U64,
        proof: Vec<B256>,
        receipt: B256,
    ) -> bool {
        let epoch_map = self.checkpoints.getter(strategy_id);
        let root = epoch_map.get(epoch_index).trades_root.get();
        if root == B256::ZERO {
            return false;
        }
        verify_proof(receipt, &proof, root)
    }

    pub fn strategy_count(&self) -> U256 {
        self.strategy_count.get()
    }

    pub fn config(&self) -> (Address, Address, Address, U256, U256, U64, bool, U256) {
        (
            self.admin.get(),
            self.resolver.get(),
            self.usdg.get(),
            self.bond_amount.get(),
            self.challenge_stake.get(),
            self.challenge_window.get(),
            self.paused.get(),
            self.treasury.get(),
        )
    }

    pub fn get_strategy(
        &self,
        strategy_id: U256,
    ) -> Result<(Address, String, String, U256, U8, U64), ProvenError> {
        let strategy = self.strategies.getter(strategy_id);
        if strategy.owner.get().is_zero() {
            return Err(ProvenError::StrategyNotFound(StrategyNotFound {}));
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

    pub fn get_performance(
        &self,
        strategy_id: U256,
    ) -> Result<(I256, I256, I256, U64, U64), ProvenError> {
        let strategy = self.strategies.getter(strategy_id);
        if strategy.owner.get().is_zero() {
            return Err(ProvenError::StrategyNotFound(StrategyNotFound {}));
        }
        Ok((
            strategy.equity.get(),
            strategy.high_water_mark.get(),
            strategy.cumulative_pnl.get(),
            strategy.epoch_count.get(),
            strategy.finalized_epochs.get(),
        ))
    }

    pub fn get_checkpoint(
        &self,
        strategy_id: U256,
        epoch_index: U64,
    ) -> Result<(I256, I256, B256, String, U8, U64, Address, U256), ProvenError> {
        if epoch_index >= self.strategies.getter(strategy_id).epoch_count.get() {
            return Err(ProvenError::EpochUnknown(EpochUnknown {}));
        }
        let epoch_map = self.checkpoints.getter(strategy_id);
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
}

// Internal helpers ------------------------------------------------------

impl Proven {
    fn only_admin(&self) -> Result<(), ProvenError> {
        if self.vm().msg_sender() != self.admin.get() {
            return Err(ProvenError::NotAdmin(NotAdmin {}));
        }
        Ok(())
    }

    fn usdg_transfer_from(
        &mut self,
        from: Address,
        to: Address,
        amount: U256,
    ) -> Result<(), ProvenError> {
        let usdg = IUSDG::new(self.usdg.get());
        let context = Call::new_mutating(self);
        let ok = usdg
            .transfer_from(self.vm(), context, from, to, amount)
            .map_err(|_| ProvenError::TokenTransferFailed(TokenTransferFailed {}))?;
        if ok {
            Ok(())
        } else {
            Err(ProvenError::TokenTransferFailed(TokenTransferFailed {}))
        }
    }

    fn usdg_transfer(&mut self, to: Address, amount: U256) -> Result<(), ProvenError> {
        let usdg = IUSDG::new(self.usdg.get());
        let context = Call::new_mutating(self);
        let ok = usdg
            .transfer(self.vm(), context, to, amount)
            .map_err(|_| ProvenError::TokenTransferFailed(TokenTransferFailed {}))?;
        if ok {
            Ok(())
        } else {
            Err(ProvenError::TokenTransferFailed(TokenTransferFailed {}))
        }
    }
}

#[cfg(test)]
mod test;
