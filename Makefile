# Covenant monorepo quality gates.
#
# `make check` is the merge gate: it runs every suite the CI runs, in the
# same order (TS -> contract -> vouch). Every target must be runnable on a
# fresh clone with only the documented prereqs installed.

TS_FILTER := --filter @covenant/
VOUCH_DIR := vouch
CONTRACT_DIR := contracts/covenant
MERKLE_DIR := contracts/merkle-core
# Any interpreter with the dev extras installed works: system python3 on
# CI, a venv or the laya venv locally (make check PYTHON=/path/to/python).
PYTHON ?= python3

.DEFAULT_GOAL := help
.PHONY: help install ts-install ts-test ts-build ts-arch ts-coverage \
        contract-test contract-lint contract-build vouch-install vouch-lint vouch-format \
        vouch-type vouch-test vouch-imports vouch-coverage check quality-fix

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

ts-arch: ## dependency-cruiser: workspace layering (sdk < indexer < web)
	pnpm exec depcruise sdk/src indexer/src web/lib web/app --config .dependency-cruiser.cjs

ts-coverage: ## coverage floors: sdk 75 lines, indexer 95 lines
	npx c8 --check-coverage --lines 75 --include "sdk/dist/src/**/*.js" \
		node --test "sdk/dist/test/*.test.js"
	npx c8 --check-coverage --lines 95 --include "indexer/dist/src/**/*.js" \
		node --test "indexer/dist/test/*.test.js"

# --- Contract (Rust) -------------------------------------------------------
contract-test: ## merkle-core + covenant cargo suites
	cd $(MERKLE_DIR) && cargo test
	cd $(CONTRACT_DIR) && cargo test

contract-lint: ## rustfmt + clippy on both Rust crates (mirrors CI)
	cd $(MERKLE_DIR) && cargo fmt --check && cargo clippy --all-targets -- -D warnings
	cd $(CONTRACT_DIR) && cargo fmt --check && cargo clippy --all-targets -- -D warnings

contract-build: ## wasm32 release build (what stylus deploy ships)
	cd $(CONTRACT_DIR) && cargo build --release --target wasm32-unknown-unknown

# --- Vouch agent (Python) --------------------------------------------------
vouch-install: ## python deps for the agent + its tooling (dev extras)
	cd $(VOUCH_DIR) && $(PYTHON) -m pip install --user -e '.[dev]'

vouch-lint: ## ruff lint + format check on the agent package
	cd $(VOUCH_DIR) && ruff check vouch tests
	cd $(VOUCH_DIR) && ruff format --check vouch tests

vouch-format: ## ruff autofix + reformat
	cd $(VOUCH_DIR) && ruff check --fix vouch tests
	cd $(VOUCH_DIR) && ruff format vouch tests

vouch-type: ## mypy on the agent package
	cd $(VOUCH_DIR) && mypy vouch

vouch-test: ## agent attestation + unit suites
	cd $(VOUCH_DIR) && $(PYTHON) -m unittest discover -s tests -v

vouch-imports: ## import-linter: package layering contracts
	cd $(VOUCH_DIR) && $(PYTHON) -m importlinter.cli --config pyproject.toml lint

vouch-coverage: ## coverage floors: package 50 lines, attest core 85
	cd $(VOUCH_DIR) && $(PYTHON) -m coverage run -m unittest discover -s tests > /dev/null
	cd $(VOUCH_DIR) && $(PYTHON) -m coverage report --fail-under=40 | tail -1
	cd $(VOUCH_DIR) && $(PYTHON) -m coverage report --include="vouch/attest/*" --fail-under=85 | tail -1

# --- Gates ------------------------------------------------------------------
check: ts-test ts-arch ts-coverage ts-build contract-test contract-lint vouch-lint \
       vouch-type vouch-imports vouch-coverage vouch-test ## the merge gate (mirrors CI)

quality-fix: vouch-format ## apply all safe autofixes
