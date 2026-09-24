//! Contract tests over the mock VM.
//!
//! ERC-20 interactions are mocked at the ABI level: the mock calldata must be
//! byte-identical to what `sol_interface!` produces (selector + encoded args),
//! which is exactly what a real USDG token would receive.

use super::*;
use alloy_primitives::{keccak256, Address, U256};
use alloy_sol_types::{sol, SolCall, SolEvent, SolType};
use stylus_sdk::testing::TestVM;

sol! {
    interface IERC20Mock {
        function transferFrom(address from, address to, uint256 amount) external returns (bool);
        function transfer(address to, uint256 amount) external returns (bool);
    }
}

const BOND: u64 = 1_000; // USDG base units
const STAKE: u64 = 100;
const WINDOW: u64 = 3600;

fn addr(seed: u8) -> Address {
    Address::from([seed; 20])
}

fn i256(v: i64) -> I256 {
    I256::try_from(v).expect("i256")
}

struct Setup {
    vm: TestVM,
    admin: Address,
    operator: Address,
    resolver: Address,
    challenger: Address,
    usdg: Address,
    bond: U256,
    stake: U256,
}

impl Setup {
    fn new() -> Self {
        let vm = TestVM::new();
        let s = Setup {
            vm: vm,
            admin: addr(1),
            operator: addr(2),
            resolver: addr(3),
            challenger: addr(4),
            usdg: addr(5),
            bond: U256::from(BOND),
            stake: U256::from(STAKE),
        };
        s
    }

    /// Deploys the contract through its real constructor as `admin`.
    fn deploy(&self) -> Proven {
        self.vm.set_sender(self.admin);
        let mut contract = Proven::from(&self.vm);
        contract
            .constructor(
                self.usdg,
                self.resolver,
                self.bond,
                self.stake,
                U64::from(U64::from(WINDOW)),
            )
            .map_err(|_| "constructor failed")
            .expect("constructor");
        contract
    }

    fn mock_transfer_from(&self, from: Address, amount: U256) {
        let calldata = IERC20Mock::transferFromCall {
            from,
            to: self.vm.contract_address(),
            amount,
        }
        .abi_encode();
        self.vm
            .mock_call(self.usdg, calldata, U256::ZERO, Ok(sol_data_bool(true)));
    }

    fn mock_transfer(&self, to: Address, amount: U256) {
        let calldata = IERC20Mock::transferCall { to, amount }.abi_encode();
        self.vm
            .mock_call(self.usdg, calldata, U256::ZERO, Ok(sol_data_bool(true)));
    }

    /// Registers a strategy as the operator, expecting the bond pull to succeed.
    fn register(&self, contract: &mut Proven) -> U256 {
        self.vm.set_sender(self.operator);
        self.mock_transfer_from(self.operator, self.bond);
        contract
            .register_strategy("jev-core".into(), "ipfs://meta".into())
            .map_err(|_| "register failed")
            .expect("register")
    }
}

fn sol_data_bool(v: bool) -> Vec<u8> {
    <alloy_sol_types::sol_data::Bool as SolType>::abi_encode(&v)
}

#[test]
fn constructor_sets_config_and_rejects_zero_inputs() {
    let s = Setup::new();
    s.vm.set_sender(s.admin);
    let mut contract = Proven::from(&s.vm);

    assert!(contract
        .constructor(Address::ZERO, s.resolver, s.bond, s.stake, U64::from(WINDOW))
        .is_err());
    assert!(contract
        .constructor(s.usdg, s.resolver, U256::ZERO, s.stake, U64::from(WINDOW))
        .is_err());

    contract
        .constructor(s.usdg, s.resolver, s.bond, s.stake, U64::from(WINDOW))
        .expect("constructor");
    let (admin, resolver, usdg, bond, stake, window, paused, treasury) = contract.config();
    assert_eq!(admin, s.admin);
    assert_eq!(resolver, s.resolver);
    assert_eq!(usdg, s.usdg);
    assert_eq!(bond, s.bond);
    assert_eq!(stake, s.stake);
    assert_eq!(window, U64::from(WINDOW));
    assert!(!paused);
    assert_eq!(treasury, U256::ZERO);
}

#[test]
fn admin_guards_block_strangers() {
    let s = Setup::new();
    let mut contract = s.deploy();

    s.vm.set_sender(s.challenger);
    assert!(contract.set_resolver(s.challenger).is_err());
    assert!(contract.pause().is_err());
    assert!(contract.set_parameters(s.bond, s.stake, U64::from(1)).is_err());
    assert!(contract.withdraw_treasury(s.challenger, U256::from(1)).is_err());
}

#[test]
fn register_strategy_pulls_bond_and_assigns_ids() {
    let s = Setup::new();
    let mut contract = s.deploy();

    let id = s.register(&mut contract);
    assert_eq!(id, U256::from(1));

    let (owner, name, uri, bond, status, _created) = contract.get_strategy(id).expect("strategy");
    assert_eq!(owner, s.operator);
    assert_eq!(name, "jev-core");
    assert_eq!(uri, "ipfs://meta");
    assert_eq!(bond, s.bond);
    assert_eq!(status, STATUS_ACTIVE);
    assert_eq!(contract.strategy_count(), U256::from(1));

    // Second registration gets the next id.
    let id2 = s.register(&mut contract);
    assert_eq!(id2, U256::from(2));

    // Unknown strategies are not found.
    assert!(contract.get_strategy(U256::from(42)).is_err());
}

#[test]
fn paused_contract_rejects_registration() {
    let s = Setup::new();
    let mut contract = s.deploy();
    s.vm.set_sender(s.admin);
    contract.pause().expect("pause");
    s.vm.set_sender(s.operator);
    s.mock_transfer_from(s.operator, s.bond);
    assert!(matches!(
        contract.register_strategy("x".into(), "y".into()),
        Err(ProvenError::ContractPaused(ContractPaused {}))
    ));
    s.vm.set_sender(s.admin);
    contract.unpause().expect("unpause");
    s.vm.set_sender(s.operator);
    assert!(contract.register_strategy("x".into(), "y".into()).is_ok());
}

#[test]
fn commit_epoch_requires_owner_and_sequential_epochs() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);

    // Stranger cannot commit.
    s.vm.set_sender(s.challenger);
    assert!(matches!(
        contract.commit_epoch(id, U64::from(0), i256(1000), I256::ZERO, B256::ZERO, "".into()),
        Err(ProvenError::NotStrategyOwner(NotStrategyOwner {}))
    ));

    // Owner cannot skip epochs.
    s.vm.set_sender(s.operator);
    assert!(matches!(
        contract.commit_epoch(id, U64::from(1), i256(1000), I256::ZERO, B256::ZERO, "".into()),
        Err(ProvenError::EpochNotSequential(EpochNotSequential {}))
    ));

    // Sequential commit succeeds; unknown strategy rejected.
    assert!(contract
        .commit_epoch(id, U64::from(0), i256(1000), I256::ZERO, B256::ZERO, "ipfs://e0".into())
        .is_ok());
    assert!(matches!(
        contract.commit_epoch(U256::from(99), U64::from(0), i256(1), I256::ZERO, B256::ZERO, "".into()),
        Err(ProvenError::StrategyNotFound(StrategyNotFound {}))
    ));
}

#[test]
fn finalize_applies_performance_accounting() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);

    // Epoch 0: initial capital of 1000 is itself the first flow, so PnL 0.
    contract
        .commit_epoch(id, U64::from(0), i256(1000), i256(1000), B256::ZERO, "".into())
        .expect("commit 0");
    // Cannot finalize inside the challenge window.
    assert!(matches!(
        contract.finalize_epoch(id, U64::from(0)),
        Err(ProvenError::ChallengeWindowActive(ChallengeWindowActive {}))
    ));

    // Epoch 1: equity 1200 after a net deposit of 100. PnL = 1200 - 1000 - 100 = 100.
    s.vm.set_block_timestamp(1);
    contract
        .commit_epoch(id, U64::from(1), i256(1200), i256(100), B256::ZERO, "".into())
        .expect("commit 1");

    // Move time past both windows and finalize out of order is not possible:
    // epoch 0 finalizes, then epoch 1.
    s.vm.set_block_timestamp(WINDOW + 1);
    contract.finalize_epoch(id, U64::from(0)).expect("finalize 0");
    contract.finalize_epoch(id, U64::from(1)).expect("finalize 1");

    let (equity, hwm, cumulative_pnl, epoch_count, finalized) =
        contract.get_performance(id).expect("performance");
    assert_eq!(equity, i256(1200));
    assert_eq!(cumulative_pnl, i256(100));
    assert_eq!(hwm, i256(1200));
    assert_eq!(epoch_count, U64::from(2));
    assert_eq!(finalized, U64::from(2));

    // Epoch 2: loss to 900. PnL = -300. HWM must stay 1200.
    s.vm.set_block_timestamp(2);
    contract
        .commit_epoch(id, U64::from(2), i256(900), I256::ZERO, B256::ZERO, "".into())
        .expect("commit 2");
    s.vm.set_block_timestamp(WINDOW + 2);
    contract.finalize_epoch(id, U64::from(2)).expect("finalize 2");
    let (equity, hwm, cumulative_pnl, _, _) = contract.get_performance(id).expect("performance");
    assert_eq!(equity, i256(900));
    assert_eq!(hwm, i256(1200));
    assert_eq!(cumulative_pnl, i256(-200));
}

#[test]
fn challenge_and_upheld_resolution_slashes_operator() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(id, U64::from(0), i256(1000), I256::ZERO, B256::ZERO, "".into())
        .expect("commit");

    // Challenge within the window.
    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, s.stake);
    contract
        .challenge_epoch(id, U64::from(0), "equity misreported".into())
        .expect("challenge");

    // Resolver upholds: stake refunded, bond slashed, strategy suspended.
    s.vm.set_sender(s.resolver);
    s.mock_transfer(s.challenger, s.stake);
    s.mock_transfer(s.challenger, s.bond);
    contract.resolve_challenge(id, U64::from(0), true).expect("resolve");

    let (_, _, _, _, status, _) = contract.get_strategy(id).expect("strategy");
    assert_eq!(status, STATUS_SUSPENDED);
    let (_, _, _, _, cp_status, _, _, _) = contract.get_checkpoint(id, U64::from(0)).expect("checkpoint");
    assert_eq!(cp_status, CP_INVALIDATED);

    // Suspended strategies cannot commit.
    s.vm.set_sender(s.operator);
    assert!(matches!(
        contract.commit_epoch(id, U64::from(1), i256(1), I256::ZERO, B256::ZERO, "".into()),
        Err(ProvenError::StrategySuspended(StrategySuspended {}))
    ));
}

#[test]
fn dismissed_challenge_forfeits_stake_and_finalizes_epoch() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(id, U64::from(0), i256(1000), I256::ZERO, B256::ZERO, "".into())
        .expect("commit");

    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, s.stake);
    contract.challenge_epoch(id, U64::from(0), "spurious".into()).expect("challenge");

    s.vm.set_sender(s.resolver);
    contract.resolve_challenge(id, U64::from(0), false).expect("resolve");

    let (_, _, _, _, _, _, _, treasury) = contract.config();
    assert_eq!(treasury, s.stake);
    let (_, _, _, _, cp_status, _, _, _) = contract.get_checkpoint(id, U64::from(0)).expect("checkpoint");
    assert_eq!(cp_status, CP_FINALIZED);

    // Finalized checkpoints cannot be challenged again.
    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, s.stake);
    assert!(matches!(
        contract.challenge_epoch(id, U64::from(0), "again".into()),
        Err(ProvenError::EpochNotPending(EpochNotPending {}))
    ));
}

#[test]
fn challenge_after_window_is_rejected() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(id, U64::from(0), i256(1000), I256::ZERO, B256::ZERO, "".into())
        .expect("commit");

    s.vm.set_block_timestamp(WINDOW + 1);
    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, s.stake);
    assert!(matches!(
        contract.challenge_epoch(id, U64::from(0), "too late".into()),
        Err(ProvenError::ChallengeWindowElapsed(ChallengeWindowElapsed {}))
    ));
}

#[test]
fn verify_receipt_checks_merkle_proofs() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);

    // Three receipts committed for the epoch.
    let leaves: Vec<B256> = (0..3u8).map(|i| keccak256([i; 32])).collect();
    let root = merkle_core::build_root(&leaves);
    contract
        .commit_epoch(id, U64::from(0), i256(1000), I256::ZERO, root, "ipfs://e".into())
        .expect("commit");

    for i in 0..3usize {
        let proof = merkle_core::build_proof(&leaves, i);
        assert!(
            contract.verify_receipt(id, U64::from(0), proof, leaves[i]),
            "receipt {i} must verify"
        );
    }
    // Foreign receipt does not verify.
    assert!(!contract.verify_receipt(id, U64::from(0), vec![leaves[0]], keccak256([0xff; 32])));
    // Zero-root (empty/unknown) epochs never verify.
    assert!(!contract.verify_receipt(id, U64::from(5), vec![], leaves[0]));
}

#[test]
fn emitted_events_carry_expected_topics() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(id, U64::from(0), i256(1000), I256::ZERO, B256::ZERO, "".into())
        .expect("commit");

    let logs = s.vm.get_emitted_logs();
    let registered_topic = <StrategyRegistered as SolEvent>::SIGNATURE_HASH;
    let committed_topic = <EpochCommitted as SolEvent>::SIGNATURE_HASH;
    assert!(
        logs.iter().any(|(topics, _)| topics.len() >= 2 && topics[0] == registered_topic),
        "StrategyRegistered must be emitted"
    );
    assert!(
        logs.iter().any(|(topics, _)| topics.len() >= 2 && topics[0] == committed_topic),
        "EpochCommitted must be emitted"
    );
}
