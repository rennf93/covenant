# Pull request

## What and why

<!-- One paragraph: what this PR changes and why it is needed. Link issues with Fixes #N. -->

## Protocol impact

<!-- Does this change the receipt encoding, hashing, or Merkle semantics? That is a protocol change (docs/protocol.md, section 4): it must be mirrored in sdk/src, contracts/merkle-core/src, and vouch/vouch/attest/, with the shared fixtures regenerated (sdk/scripts/gen-fixtures.ts) and all conformance suites green. If not, write "none". -->

## Testing done

<!-- Which gates did you run? Tests, build, lint, manual runs against a deployed contract. Include actual counts. -->

- [ ] SDK tests (`pnpm --filter @covenant/sdk test`)
- [ ] Indexer tests (`pnpm --filter @covenant/indexer test`)
- [ ] Web build (`pnpm --filter @covenant/web run build`)
- [ ] Contract tests (`cd contracts/covenant && cargo test`)
- [ ] merkle-core tests (`cd contracts/merkle-core && cargo test`)
- [ ] vouch gates (`ruff check`, `ruff format --check`, `mypy`, `python3 -m unittest discover -s tests`)

## Checklist

- [ ] Tests are green for every component I touched
- [ ] Docs updated where behavior, paths, or cited symbols changed (including evidence citations in docs/protocol.md)
- [ ] No em-dashes or en-dashes anywhere in this diff (plain hyphens only)
- [ ] No AI attribution anywhere (no Co-Authored-By trailer, no "Generated with" footer)
- [ ] Test counts and claims in docs/READMEs still match reality
