//! Contract surface types: events, the USDG token interface, errors, and
//! lifecycle constants. Everything here is part of the ABI.

use alloy_sol_types::sol;
use stylus_sdk::prelude::*;

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

/// The contract's error set. One variant per sol! error above so revert data
/// is always decodable.
#[derive(SolidityError)]
pub enum CovenantError {
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

impl core::fmt::Debug for CovenantError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        let name = match self {
            CovenantError::NotAdmin(_) => "NotAdmin",
            CovenantError::NotResolver(_) => "NotResolver",
            CovenantError::NotStrategyOwner(_) => "NotStrategyOwner",
            CovenantError::ContractPaused(_) => "ContractPaused",
            CovenantError::StrategyNotFound(_) => "StrategyNotFound",
            CovenantError::StrategySuspended(_) => "StrategySuspended",
            CovenantError::EpochNotSequential(_) => "EpochNotSequential",
            CovenantError::EpochUnknown(_) => "EpochUnknown",
            CovenantError::EpochNotPending(_) => "EpochNotPending",
            CovenantError::ChallengeWindowElapsed(_) => "ChallengeWindowElapsed",
            CovenantError::ChallengeWindowActive(_) => "ChallengeWindowActive",
            CovenantError::AlreadyChallenged(_) => "AlreadyChallenged",
            CovenantError::ZeroAddress(_) => "ZeroAddress",
            CovenantError::ZeroAmount(_) => "ZeroAmount",
            CovenantError::TokenTransferFailed(_) => "TokenTransferFailed",
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
