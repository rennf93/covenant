//! Storage layout. `Covenant` is the entrypoint storage type; `Strategy` and
//! `Checkpoint` are the mapping value types. The layout is consensus-critical:
//! field order changes are upgrades and must be treated as such.

use stylus_sdk::prelude::*;

sol_storage! {
    #[entrypoint]
    pub struct Covenant {
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
