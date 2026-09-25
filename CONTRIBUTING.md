# Contributing

**Audience:** anyone wanting to change this monorepo. This file covers
per-component setup, the quality gates every change must pass, and the commit
and PR conventions. **Scope:** the repo as of v0.1.0. For what the components
are, read [docs/architecture.md](docs/architecture.md) first.

## Repo layout

| Path | Language | Package manager |
| --- | --- | --- |
| `contracts/covenant/`, `contracts/merkle-core/` | Rust (Stylus) | cargo |
| `sdk/`, `indexer/`, `web/` | TypeScript | pnpm (workspace) |
| `vouch/` | Python 3.12 | pip/venv (ruff + mypy) |

## Setup per component

```bash
# TypeScript workspace (sdk, indexer, web)
pnpm install

# Rust contracts
cd contracts/covenant && cargo test          # 11/11
cd contracts/merkle-core && cargo test       # 6/6 (incl. cross-language fixtures)

# vouch agent
cd vouch
python3.12 -m venv .venv
.venv/bin/python -m pip install httpx pydantic-settings websockets
# attestation conformance tests need the SDK built once:
#   pnpm --filter @covenant/sdk run build   (from the repo root)
.venv/bin/python -m unittest discover -s tests   # 10/10
```

Component-specific run instructions are in each README
(`sdk/README.md`, `indexer/README.md`, `web/README.md`, `vouch/README.md`).

## Quality gates

Everything below runs in CI (`.github/workflows/ci.yml`) and must be green
before merge. Run the relevant subset locally first.

| Gate | Command |
| --- | --- |
| SDK + indexer tests | `pnpm -r test` |
| Web build | `pnpm --filter @covenant/web run build` |
| Contract tests | `cd contracts/covenant && cargo test` |
| merkle-core tests | `cd contracts/merkle-core && cargo test` |
| WASM release build | `cd contracts/covenant && cargo build --release --target wasm32-unknown-unknown` |
| vouch lint | `cd vouch && ruff check vouch tests && ruff format --check vouch tests` |
| vouch types | `cd vouch && mypy vouch` |
| vouch attestation tests | `cd vouch && python3 -m unittest discover -s tests` |

Hard rules beyond the gates:

- **Protocol changes touch three implementations plus fixtures.** If you
  change the receipt encoding, hashing, or Merkle semantics, you must mirror
  it in `sdk/src/receipt.ts` + `sdk/src/merkle.ts`,
  `contracts/merkle-core/src/lib.rs`, and `vouch/vouch/attest/`, and
  regenerate the shared fixtures. This is a protocol change per
  [docs/protocol.md](docs/protocol.md), section 4; do not do it casually.
- **Docs cite code.** Normative statements in `docs/protocol.md` carry
  `(evidence: file::symbol)` citations; if your change moves or renames a
  cited symbol, update the citations in the same PR.
- **No fake precision.** Test counts and claims in READMEs and docs must
  match reality.

## Commit conventions

- Conventional-ish subjects: an imperative summary line, scoped when useful,
  e.g. `Indexer: guard against empty backfill range`, `sdk: reject >8dp
  prices`. Existing history is a good guide.
- Plain hyphen-minus only. Never em-dashes or en-dashes in commit messages,
  code, comments, docs, or PR text.
- No AI attribution of any kind: no `Co-Authored-By: Claude <...>` trailer,
  no "Generated with ..." footer. Commits stand on their own.
- Keep commits focused: one behavior or component per commit where
  practical.

## Pull requests

Use the PR template (`.github/PULL_REQUEST_TEMPLATE.md`). The checklist:

- tests green for every component you touched (table above);
- docs updated if you changed behavior, paths, or claims;
- no em-dashes or en-dashes anywhere in the diff;
- no AI attribution anywhere in the PR;
- protocol changes (if any) mirrored in all three implementations with
  regenerated fixtures.

## Reporting issues

Use the issue templates (`.github/ISSUE_TEMPLATE/`); pick the component
dropdown so the right person sees it. Security issues are different: see
[SECURITY.md](SECURITY.md) and report privately.
