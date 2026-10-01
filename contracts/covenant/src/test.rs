//! Contract tests over the mock VM.
//!
//! ERC-20 interactions are mocked at the ABI level: the mock calldata must be
//! byte-identical to what `sol_interface!` produces (selector + encoded args),
//! which is exactly what a real USDG token would receive.

use super::*;
use alloy_primitives::{Address, U256, keccak256};
use alloy_sol_types::{SolCall, SolEvent, SolType, sol};
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
        Setup {
            vm,
            admin: addr(1),
            operator: addr(2),
            resolver: addr(3),
            challenger: addr(4),
            usdg: addr(5),
            bond: U256::from(BOND),
            stake: U256::from(STAKE),
        }
    }

    /// Deploys the contract through its real constructor as `admin`.
    fn deploy(&self) -> Covenant {
        self.vm.set_sender(self.admin);
        let mut contract = Covenant::from(&self.vm);
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
    fn register(&self, contract: &mut Covenant) -> U256 {
        self.vm.set_sender(self.operator);
        self.mock_transfer_from(self.operator, self.bond);
        contract
            .register_strategy("jev-core".into(), "ipfs://meta".into())
            .map_err(|_| "register failed")
            .expect("register")
    }

    /// Registers, then commits epoch 0 (equity 1000, no net flow) as the operator.
    fn register_and_commit_epoch0(&self, contract: &mut Covenant) -> U256 {
        let id = self.register(contract);
        self.vm.set_sender(self.operator);
        contract
            .commit_epoch(
                id,
                U64::from(0),
                i256(1000),
                I256::ZERO,
                B256::ZERO,
                "".into(),
            )
            .expect("commit epoch 0");
        id
    }

    /// Challenges epoch 0 as the challenger with the stake pull mocked.
    fn challenge_epoch0(&self, contract: &mut Covenant, id: U256) {
        self.vm.set_sender(self.challenger);
        self.mock_transfer_from(self.challenger, self.stake);
        contract
            .challenge_epoch(id, U64::from(0), "equity misreported".into())
            .expect("challenge epoch 0");
    }
}

fn sol_data_bool(v: bool) -> Vec<u8> {
    <alloy_sol_types::sol_data::Bool as SolType>::abi_encode(&v)
}

#[test]
fn constructor_sets_config_and_rejects_zero_inputs() {
    let s = Setup::new();
    s.vm.set_sender(s.admin);
    let mut contract = Covenant::from(&s.vm);

    assert!(
        contract
            .constructor(
                Address::ZERO,
                s.resolver,
                s.bond,
                s.stake,
                U64::from(WINDOW)
            )
            .is_err()
    );
    assert!(
        contract
            .constructor(s.usdg, s.resolver, U256::ZERO, s.stake, U64::from(WINDOW))
            .is_err()
    );

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
    assert!(
        contract
            .set_parameters(s.bond, s.stake, U64::from(1))
            .is_err()
    );
    assert!(
        contract
            .withdraw_treasury(s.challenger, U256::from(1))
            .is_err()
    );
    assert!(contract.transfer_admin(s.challenger).is_err());
    assert!(contract.accept_admin().is_err());
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
        Err(CovenantError::ContractPaused(ContractPaused {}))
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
        contract.commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into()
        ),
        Err(CovenantError::NotStrategyOwner(NotStrategyOwner {}))
    ));

    // Owner cannot skip epochs.
    s.vm.set_sender(s.operator);
    assert!(matches!(
        contract.commit_epoch(
            id,
            U64::from(1),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into()
        ),
        Err(CovenantError::EpochNotSequential(EpochNotSequential {}))
    ));

    // Sequential commit succeeds; unknown strategy rejected.
    assert!(
        contract
            .commit_epoch(
                id,
                U64::from(0),
                i256(1000),
                I256::ZERO,
                B256::ZERO,
                "ipfs://e0".into()
            )
            .is_ok()
    );
    assert!(matches!(
        contract.commit_epoch(
            U256::from(99),
            U64::from(0),
            i256(1),
            I256::ZERO,
            B256::ZERO,
            "".into()
        ),
        Err(CovenantError::StrategyNotFound(StrategyNotFound {}))
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
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            i256(1000),
            B256::ZERO,
            "".into(),
        )
        .expect("commit 0");
    // Cannot finalize inside the challenge window.
    assert!(matches!(
        contract.finalize_epoch(id, U64::from(0)),
        Err(CovenantError::ChallengeWindowActive(
            ChallengeWindowActive {}
        ))
    ));

    // Epochs settle strictly in order: epoch 1 commits only after epoch 0
    // has finalized, so it can never be finalized before its predecessor.
    s.vm.set_block_timestamp(WINDOW + 1);
    contract
        .finalize_epoch(id, U64::from(0))
        .expect("finalize 0");

    // Epoch 1: equity 1200 after a net deposit of 100. PnL = 1200 - 1000 - 100 = 100.
    contract
        .commit_epoch(
            id,
            U64::from(1),
            i256(1200),
            i256(100),
            B256::ZERO,
            "".into(),
        )
        .expect("commit 1");
    s.vm.set_block_timestamp(2 * (WINDOW + 1));
    contract
        .finalize_epoch(id, U64::from(1))
        .expect("finalize 1");

    let (equity, hwm, cumulative_pnl, epoch_count, finalized) =
        contract.get_performance(id).expect("performance");
    assert_eq!(equity, i256(1200));
    assert_eq!(cumulative_pnl, i256(100));
    assert_eq!(hwm, i256(1200));
    assert_eq!(epoch_count, U64::from(2));
    assert_eq!(finalized, U64::from(2));

    // Epoch 2: loss to 900. PnL = -300. HWM must stay 1200.
    contract
        .commit_epoch(
            id,
            U64::from(2),
            i256(900),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit 2");
    s.vm.set_block_timestamp(3 * (WINDOW + 1));
    contract
        .finalize_epoch(id, U64::from(2))
        .expect("finalize 2");
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
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
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
    contract
        .resolve_challenge(id, U64::from(0), true)
        .expect("resolve");

    let (_, _, _, _, status, _) = contract.get_strategy(id).expect("strategy");
    assert_eq!(status, STATUS_SUSPENDED);
    let (_, _, _, _, cp_status, _, _, _) = contract
        .get_checkpoint(id, U64::from(0))
        .expect("checkpoint");
    assert_eq!(cp_status, CP_INVALIDATED);

    // Suspended strategies cannot commit.
    s.vm.set_sender(s.operator);
    assert!(matches!(
        contract.commit_epoch(id, U64::from(1), i256(1), I256::ZERO, B256::ZERO, "".into()),
        Err(CovenantError::StrategySuspended(StrategySuspended {}))
    ));
}

#[test]
fn dismissed_challenge_forfeits_stake_and_finalizes_epoch() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit");

    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, s.stake);
    contract
        .challenge_epoch(id, U64::from(0), "spurious".into())
        .expect("challenge");

    s.vm.set_sender(s.resolver);
    contract
        .resolve_challenge(id, U64::from(0), false)
        .expect("resolve");

    let (_, _, _, _, _, _, _, treasury) = contract.config();
    assert_eq!(treasury, s.stake);
    let (_, _, _, _, cp_status, _, _, _) = contract
        .get_checkpoint(id, U64::from(0))
        .expect("checkpoint");
    assert_eq!(cp_status, CP_FINALIZED);

    // Finalized checkpoints cannot be challenged again.
    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, s.stake);
    assert!(matches!(
        contract.challenge_epoch(id, U64::from(0), "again".into()),
        Err(CovenantError::EpochNotPending(EpochNotPending {}))
    ));
}

#[test]
fn challenge_after_window_is_rejected() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit");

    s.vm.set_block_timestamp(WINDOW + 1);
    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, s.stake);
    assert!(matches!(
        contract.challenge_epoch(id, U64::from(0), "too late".into()),
        Err(CovenantError::ChallengeWindowElapsed(
            ChallengeWindowElapsed {}
        ))
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
    let root = crate::merkle_core::build_root(&leaves);
    contract
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            root,
            "ipfs://e".into(),
        )
        .expect("commit");

    for i in 0..3usize {
        let proof = crate::merkle_core::build_proof(&leaves, i);
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
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit");

    let logs = s.vm.get_emitted_logs();
    let registered_topic = <StrategyRegistered as SolEvent>::SIGNATURE_HASH;
    let committed_topic = <EpochCommitted as SolEvent>::SIGNATURE_HASH;
    assert!(
        logs.iter()
            .any(|(topics, _)| topics.len() >= 2 && topics[0] == registered_topic),
        "StrategyRegistered must be emitted"
    );
    assert!(
        logs.iter()
            .any(|(topics, _)| topics.len() >= 2 && topics[0] == committed_topic),
        "EpochCommitted must be emitted"
    );
}

// Phantom-epoch regressions -------------------------------------------

#[test]
fn finalize_unknown_epoch_or_strategy_reverts() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);

    // Regression: a never-committed epoch used to read zero-initialized
    // storage as Pending (committed_at 0), so anyone could "finalize" it and
    // apply pnl = 0 - equity, zeroing the strategy.
    assert!(matches!(
        contract.finalize_epoch(id, U64::from(0)),
        Err(CovenantError::EpochUnknown(EpochUnknown {}))
    ));
    assert!(matches!(
        contract.finalize_epoch(U256::from(99), U64::from(0)),
        Err(CovenantError::StrategyNotFound(StrategyNotFound {}))
    ));
    let (_, _, _, _, finalized) = contract.get_performance(id).expect("performance");
    assert_eq!(finalized, U64::from(0), "no phantom accounting may apply");
}

#[test]
fn challenge_unknown_epoch_or_strategy_reverts() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);

    // Regression: inside the first window a phantom epoch passed the status
    // and window checks (zero storage reads as Pending at time 0) and the
    // stake would have been escrowed against nothing.
    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, s.stake);
    assert!(matches!(
        contract.challenge_epoch(id, U64::from(0), "phantom".into()),
        Err(CovenantError::EpochUnknown(EpochUnknown {}))
    ));
    assert!(matches!(
        contract.challenge_epoch(U256::from(99), U64::from(0), "ghost".into()),
        Err(CovenantError::StrategyNotFound(StrategyNotFound {}))
    ));
}

// Sequential accountability --------------------------------------------

#[test]
fn commit_blocks_until_previous_epoch_finalizes() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit 0");

    // Regression: committing epoch 1 while epoch 0 was still pending used to
    // succeed, letting epochs outrun their accountability.
    s.vm.set_block_timestamp(WINDOW + 1);
    assert!(matches!(
        contract.commit_epoch(
            id,
            U64::from(1),
            i256(1200),
            I256::ZERO,
            B256::ZERO,
            "".into()
        ),
        Err(CovenantError::PreviousEpochNotFinalized(
            PreviousEpochNotFinalized {}
        ))
    ));

    // Once epoch 0 settles, epoch 1 goes through.
    contract
        .finalize_epoch(id, U64::from(0))
        .expect("finalize 0");
    contract
        .commit_epoch(
            id,
            U64::from(1),
            i256(1200),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit 1");
}

#[test]
fn challenged_epoch_blocks_commits_until_resolved() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit 0");
    s.challenge_epoch0(&mut contract, id);

    // A Challenged checkpoint is not Finalized: it blocks later commits.
    s.vm.set_block_timestamp(WINDOW + 1);
    s.vm.set_sender(s.operator);
    assert!(matches!(
        contract.commit_epoch(
            id,
            U64::from(1),
            i256(1200),
            I256::ZERO,
            B256::ZERO,
            "".into()
        ),
        Err(CovenantError::PreviousEpochNotFinalized(
            PreviousEpochNotFinalized {}
        ))
    ));

    // Dismissal finalizes epoch 0 and unblocks the strategy.
    s.vm.set_sender(s.resolver);
    contract
        .resolve_challenge(id, U64::from(0), false)
        .expect("resolve");
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(
            id,
            U64::from(1),
            i256(1200),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit 1");
}

#[test]
fn finalize_enforces_sequential_order() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);

    // Defense in depth: commit refuses to create two pending checkpoints,
    // so craft them directly and verify finalize still refuses to skip past
    // an unsettled predecessor.
    {
        let mut strategy = contract.strategies.setter(id);
        strategy.epoch_count.set(U64::from(2));
    }
    {
        let mut epoch_map = contract.checkpoints.setter(id);
        for i in 0..2u64 {
            let mut cp = epoch_map.setter(U64::from(i));
            cp.status.set(U8::from(CP_PENDING));
            cp.committed_at.set(U64::from(1));
        }
    }

    s.vm.set_block_timestamp(WINDOW + 1);
    assert!(matches!(
        contract.finalize_epoch(id, U64::from(1)),
        Err(CovenantError::PreviousEpochNotFinalized(
            PreviousEpochNotFinalized {}
        ))
    ));
    contract
        .finalize_epoch(id, U64::from(0))
        .expect("finalize 0");
    contract
        .finalize_epoch(id, U64::from(1))
        .expect("finalize 1 after its predecessor");
}

// Challenge game edges --------------------------------------------------

#[test]
fn double_challenge_while_challenged_reverts() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit 0");

    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, s.stake);
    contract
        .challenge_epoch(id, U64::from(0), "first".into())
        .expect("challenge");

    // A second challenger cannot pile on: only one challenger at a time.
    let second = addr(6);
    s.vm.set_sender(second);
    s.mock_transfer_from(second, s.stake);
    assert!(matches!(
        contract.challenge_epoch(id, U64::from(0), "second".into()),
        Err(CovenantError::EpochNotPending(EpochNotPending {}))
    ));
    let (_, _, _, _, _, _, challenger, _) = contract
        .get_checkpoint(id, U64::from(0))
        .expect("checkpoint");
    assert_eq!(challenger, s.challenger, "original challenger kept");
}

#[test]
fn resolve_on_non_challenged_epoch_reverts() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit 0");

    // Nothing to resolve: the checkpoint is Pending, not Challenged.
    s.vm.set_sender(s.resolver);
    assert!(matches!(
        contract.resolve_challenge(id, U64::from(0), false),
        Err(CovenantError::NotChallenged(NotChallenged {}))
    ));
}

// Pause coverage ---------------------------------------------------------

#[test]
fn paused_contract_rejects_challenge_and_resolve() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit 0");
    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, s.stake);
    contract
        .challenge_epoch(id, U64::from(0), "before pause".into())
        .expect("challenge");

    s.vm.set_sender(s.admin);
    contract.pause().expect("pause");

    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, s.stake);
    assert!(matches!(
        contract.challenge_epoch(id, U64::from(0), "paused".into()),
        Err(CovenantError::ContractPaused(ContractPaused {}))
    ));
    s.vm.set_sender(s.resolver);
    assert!(matches!(
        contract.resolve_challenge(id, U64::from(0), false),
        Err(CovenantError::ContractPaused(ContractPaused {}))
    ));

    // Unpausing restores the game where it left off.
    s.vm.set_sender(s.admin);
    contract.unpause().expect("unpause");
    s.vm.set_sender(s.resolver);
    contract
        .resolve_challenge(id, U64::from(0), false)
        .expect("resolve after unpause");
}

#[test]
fn finalize_is_not_gated_by_pause() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit 0");

    // Finalization only settles accounting after the window; it moves no
    // tokens, so pausing it would gain nothing and could strand performance
    // accounting. It stays permissionless even while paused.
    s.vm.set_sender(s.admin);
    contract.pause().expect("pause");
    s.vm.set_block_timestamp(WINDOW + 1);
    s.vm.set_sender(s.challenger);
    contract
        .finalize_epoch(id, U64::from(0))
        .expect("finalize while paused");
    let (_, _, _, _, cp_status, _, _, _) = contract
        .get_checkpoint(id, U64::from(0))
        .expect("checkpoint");
    assert_eq!(cp_status, CP_FINALIZED);
}

// Treasury ---------------------------------------------------------------

#[test]
fn withdraw_treasury_happy_path_and_overdraw() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit 0");
    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, s.stake);
    contract
        .challenge_epoch(id, U64::from(0), "forfeit me".into())
        .expect("challenge");
    s.vm.set_sender(s.resolver);
    contract
        .resolve_challenge(id, U64::from(0), false)
        .expect("dismiss");
    let (_, _, _, _, _, _, _, treasury) = contract.config();
    assert_eq!(treasury, s.stake);

    s.vm.set_sender(s.admin);
    s.mock_transfer(s.challenger, s.stake);
    contract
        .withdraw_treasury(s.challenger, s.stake)
        .expect("withdraw");
    let (_, _, _, _, _, _, _, treasury) = contract.config();
    assert_eq!(treasury, U256::ZERO);

    // Overdraw gets its own error, not a generic zero-amount rejection.
    assert!(matches!(
        contract.withdraw_treasury(s.challenger, U256::from(1)),
        Err(CovenantError::TreasuryOverdraw(TreasuryOverdraw {}))
    ));
}

// Parameters --------------------------------------------------------------

#[test]
fn set_parameters_applies_only_to_new_activity() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register(&mut contract);

    let new_bond = s.bond * U256::from(2);
    let new_stake = s.stake * U256::from(2);
    let new_window = U64::from(WINDOW / 2);
    s.vm.set_sender(s.admin);
    contract
        .set_parameters(new_bond, new_stake, new_window)
        .expect("set_parameters");
    let (_, _, _, bond, stake, window, _, _) = contract.config();
    assert_eq!(bond, new_bond);
    assert_eq!(stake, new_stake);
    assert_eq!(window, new_window);

    // The existing strategy keeps the bond it actually escrowed.
    let (_, _, _, escrowed, _, _) = contract.get_strategy(id).expect("strategy");
    assert_eq!(escrowed, s.bond);

    // A new registration escrows the new bond.
    let id2 = s.register(&mut contract);
    let (_, _, _, escrowed2, _, _) = contract.get_strategy(id2).expect("strategy 2");
    assert_eq!(escrowed2, new_bond);

    // A challenge after the change stakes the new amount.
    s.vm.set_sender(s.operator);
    contract
        .commit_epoch(
            id,
            U64::from(0),
            i256(1000),
            I256::ZERO,
            B256::ZERO,
            "".into(),
        )
        .expect("commit 0");
    s.vm.set_sender(s.challenger);
    s.mock_transfer_from(s.challenger, new_stake);
    contract
        .challenge_epoch(id, U64::from(0), "new stake".into())
        .expect("challenge");
    let (_, _, _, _, _, _, _, staked) = contract
        .get_checkpoint(id, U64::from(0))
        .expect("checkpoint");
    assert_eq!(staked, new_stake);
}

// Admin handover -----------------------------------------------------------

#[test]
fn admin_transfer_requires_two_steps() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let successor = addr(6);

    s.vm.set_sender(s.admin);
    contract.transfer_admin(successor).expect("propose");
    assert_eq!(contract.pending_admin(), successor);
    // The current admin keeps full authority until acceptance.
    contract.set_resolver(s.resolver).expect("still admin");

    // Only the proposed successor can accept.
    s.vm.set_sender(s.challenger);
    assert!(matches!(
        contract.accept_admin(),
        Err(CovenantError::NotPendingAdmin(NotPendingAdmin {}))
    ));

    s.vm.set_sender(successor);
    contract.accept_admin().expect("accept");
    let (admin, _, _, _, _, _, _, _) = contract.config();
    assert_eq!(admin, successor);
    assert_eq!(contract.pending_admin(), Address::ZERO);

    // The proposal is consumed; the old admin is powerless.
    assert!(matches!(
        contract.accept_admin(),
        Err(CovenantError::NotPendingAdmin(NotPendingAdmin {}))
    ));
    s.vm.set_sender(s.admin);
    assert!(matches!(
        contract.set_resolver(s.admin),
        Err(CovenantError::NotAdmin(NotAdmin {}))
    ));
}

#[test]
fn admin_transfer_proposal_can_be_cancelled_with_zero_address() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let successor = addr(6);

    s.vm.set_sender(s.admin);
    contract.transfer_admin(successor).expect("propose");
    assert_eq!(contract.pending_admin(), successor);

    contract
        .transfer_admin(Address::ZERO)
        .expect("cancel proposal");
    assert_eq!(contract.pending_admin(), Address::ZERO);

    s.vm.set_sender(successor);
    assert!(matches!(
        contract.accept_admin(),
        Err(CovenantError::NotPendingAdmin(NotPendingAdmin {}))
    ));
}

// Resolver deadline ----------------------------------------------------------

#[test]
fn permissionless_resolve_before_deadline_reverts() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register_and_commit_epoch0(&mut contract);
    s.challenge_epoch0(&mut contract, id);

    // One tick before the deadline a non-resolver is still turned away.
    s.vm.set_block_timestamp(4 * WINDOW - 1);
    s.vm.set_sender(s.challenger);
    assert!(matches!(
        contract.resolve_challenge(id, U64::from(0), false),
        Err(CovenantError::NotResolver(NotResolver {}))
    ));
    let (_, _, _, _, cp_status, _, _, _) = contract
        .get_checkpoint(id, U64::from(0))
        .expect("checkpoint");
    assert_eq!(cp_status, CP_CHALLENGED);
}

#[test]
fn forced_dismiss_after_deadline_refunds_stake() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register_and_commit_epoch0(&mut contract);
    s.challenge_epoch0(&mut contract, id);

    // At the deadline anyone may resolve; the decision is forced-dismiss:
    // stake refunded, epoch finalized, treasury untouched.
    s.vm.set_block_timestamp(4 * WINDOW);
    s.vm.set_sender(s.challenger);
    s.mock_transfer(s.challenger, s.stake);
    contract
        .resolve_challenge(id, U64::from(0), false)
        .expect("forced resolve");
    let (_, _, _, _, cp_status, _, _, _) = contract
        .get_checkpoint(id, U64::from(0))
        .expect("checkpoint");
    assert_eq!(cp_status, CP_FINALIZED);
    let (_, _, _, _, _, _, _, treasury) = contract.config();
    assert_eq!(treasury, U256::ZERO, "forced dismissal credits nothing");
    let (_, _, _, _, status, _) = contract.get_strategy(id).expect("strategy");
    assert_eq!(status, STATUS_ACTIVE, "no bond was slashed");

    let logs = s.vm.get_emitted_logs();
    let resolved_topic = <ChallengeResolved as SolEvent>::SIGNATURE_HASH;
    let (_, data) = logs
        .iter()
        .find(|(topics, _)| topics.first() == Some(&resolved_topic))
        .expect("ChallengeResolved emitted");
    let (upheld, forced) = <(
        alloy_sol_types::sol_data::Bool,
        alloy_sol_types::sol_data::Bool,
    ) as SolType>::abi_decode(data)
    .expect("decode");
    assert!(!upheld);
    assert!(forced, "the event must mark the forced resolution");
}

#[test]
fn forced_resolution_ignores_the_upheld_argument() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register_and_commit_epoch0(&mut contract);
    s.challenge_epoch0(&mut contract, id);

    // upheld = true on the forced path must not slash anyone: nobody decided.
    s.vm.set_block_timestamp(4 * WINDOW);
    s.vm.set_sender(addr(7)); // a bystander, not the resolver
    s.mock_transfer(s.challenger, s.stake);
    contract
        .resolve_challenge(id, U64::from(0), true)
        .expect("forced resolve");
    let (_, _, _, _, cp_status, _, _, _) = contract
        .get_checkpoint(id, U64::from(0))
        .expect("checkpoint");
    assert_eq!(cp_status, CP_FINALIZED, "dismissed, not invalidated");
    let (_, _, _, bond, status, _) = contract.get_strategy(id).expect("strategy");
    assert_eq!(bond, s.bond, "bond untouched");
    assert_eq!(status, STATUS_ACTIVE);
}

#[test]
fn resolver_still_resolves_past_deadline_without_forcing() {
    let s = Setup::new();
    let mut contract = s.deploy();
    let id = s.register_and_commit_epoch0(&mut contract);
    s.challenge_epoch0(&mut contract, id);

    // The resolver's authority does not expire at the deadline; a genuine
    // resolver dismissal still forfeits the stake to the treasury.
    s.vm.set_block_timestamp(4 * WINDOW + 1);
    s.vm.set_sender(s.resolver);
    contract
        .resolve_challenge(id, U64::from(0), false)
        .expect("resolver dismiss");
    let (_, _, _, _, _, _, _, treasury) = contract.config();
    assert_eq!(treasury, s.stake);
}
