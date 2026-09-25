# Covenant monorepo quality gates.
#
# `make check` is the merge gate: it runs every suite the CI runs, in the
# same order (TS -> contract -> vouch). Every target must be runnable on a
# fresh clone with only the documented prereqs installed.

TS_FILTER := --filter @covenant/
VOUCH_DIR := vouch
CONTRACT_DIR := contracts/covenant
MERKLE_DIR := contracts/merkle-core

.DEFAULT_GOAL := help
.PHONY: help install ts-install ts-test ts-build contract-test contract-build \
        vouch-install vouch-lint vouch-format vouch-type vouch-test \
        check quality-fix

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ts-install vouch-install ## Install everything for local development

# --- TypeScript workspace (sdk, indexer, web) -----------------------------
ts-install: ## pnpm install for the workspace
	pnpm install

ts-test: ## sdk + indexer suites
	pnpm -r test

ts-build: ## web production build
	pnpm --filter @covenant/web run build

# --- Contract (Rust) -------------------------------------------------------
contract-test: ## merkle-core + covenant cargo suites
	cd $(MERKLE_DIR) && cargo test
	cd $(CONTRACT_DIR) && cargo test

contract-build: ## wasm32 release build (what stylus deploy ships)
	cd $(CONTRACT_DIR) && cargo build --release --target wasm32-unknown-unknown

# --- Vouch agent (Python) --------------------------------------------------
vouch-install: ## python deps for the agent + its tooling (dev extras)
	cd $(VOUCH_DIR) && python3 -m pip install --user -e '.[dev]'

vouch-lint: ## ruff lint + format check on the agent package
	cd $(VOUCH_DIR) && ruff check vouch tests
	cd $(VOUCH_DIR) && ruff format --check vouch tests

vouch-format: ## ruff autofix + reformat
	cd $(VOUCH_DIR) && ruff check --fix vouch tests
	cd $(VOUCH_DIR) && ruff format vouch tests

vouch-type: ## mypy on the agent package
	cd $(VOUCH_DIR) && mypy vouch

vouch-test: ## agent attestation + unit suites
	cd $(VOUCH_DIR) && python3 -m unittest discover -s tests -v

# --- Gates ------------------------------------------------------------------
check: ts-test ts-build contract-test vouch-lint vouch-type vouch-test ## the merge gate (mirrors CI)

quality-fix: vouch-format ## apply all safe autofixes
