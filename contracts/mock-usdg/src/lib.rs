//! Mock USDG - a 6-decimal test token for Covenant staging on Robinhood
//! Chain testnet. NOT Paxos USDG: this is a throwaway test token, clearly
//! labeled as mock per the deploy runbook. Public mint by design - it is a
//! test utility, not a production asset.

#![cfg_attr(not(any(test, feature = "export-abi")), no_main)]
extern crate alloc;

use alloy_primitives::{Address, U256};
use stylus_sdk::prelude::*;

sol_storage! {
    #[entrypoint]
    pub struct MockUsdg {
        mapping(address => uint256) balances;
        mapping(address => mapping(address => uint256)) allowances;
        uint256 total_supply;
    }
}

#[public]
impl MockUsdg {
    pub fn name(&self) -> String {
        "Mock USDG (testnet only - not Paxos USDG)".to_string()
    }

    pub fn symbol(&self) -> String {
        "USDG".to_string()
    }

    pub fn decimals(&self) -> u8 {
        6
    }

    pub fn total_supply(&self) -> U256 {
        self.total_supply.get()
    }

    pub fn balance_of(&self, owner: Address) -> U256 {
        self.balances.get(owner)
    }

    /// Anyone can mint: this is a test utility token.
    pub fn mint(&mut self, to: Address, amount: U256) {
        let b = self.balances.get(to);
        self.balances.insert(to, b + amount);
        let s = self.total_supply.get();
        self.total_supply.set(s + amount);
    }

    pub fn transfer(&mut self, to: Address, amount: U256) -> bool {
        let from = self.vm().msg_sender();
        let b = self.balances.get(from);
        if b < amount {
            return false;
        }
        self.balances.insert(from, b - amount);
        let tb = self.balances.get(to);
        self.balances.insert(to, tb + amount);
        true
    }

    pub fn approve(&mut self, spender: Address, amount: U256) -> bool {
        let owner = self.vm().msg_sender();
        self.allowances.setter(owner).insert(spender, amount);
        true
    }

    pub fn allowance(&self, owner: Address, spender: Address) -> U256 {
        self.allowances.get(owner).get(spender)
    }

    pub fn transfer_from(&mut self, from: Address, to: Address, amount: U256) -> bool {
        let spender = self.vm().msg_sender();
        let allow = self.allowances.get(from).get(spender);
        if allow < amount {
            return false;
        }
        self.allowances.setter(from).insert(spender, allow - amount);
        let b = self.balances.get(from);
        if b < amount {
            return false;
        }
        self.balances.insert(from, b - amount);
        let tb = self.balances.get(to);
        self.balances.insert(to, tb + amount);
        true
    }
}
