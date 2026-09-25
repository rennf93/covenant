# Security Policy

**Audience:** anyone who found a security problem in Covenant. Kept honest
and small, because this is a hackathon project.

## Supported scope

The current `main` branch of this repository, specifically:

- the Stylus contract (`contracts/covenant/`): funds handling (USDG bonds,
  challenger stakes, treasury), access control, checkpoint and challenge
  state machines;
- the agent's key handling and chain bridge (`vouch/vouch/attest/`):
  private key use onchain;
- the dashboard/server (`vouch/vouch/server/`, `run_ui.py`): anything that
  spawns processes or holds API keys.

Out of scope: the indexer and web app (no keys, no funds; read-only), the
demo server, and anything outside this repository. Do not report third-party
dependency vulnerabilities here; use the dependency's own channel.

## How to report

Report privately. Do not open a public issue for anything fund-related or
key-related.

- GitHub: use "Report a vulnerability" under the repository's Security tab,
  or contact a maintainer directly (see the repository owner profile).
- Include: component and file, a reproduction or PoC, impact, and your
  contact if you want credit.

## What to expect

This is a hackathon project with no bug bounty and no SLA. What we commit to:

- acknowledgment within a reasonable timeframe (days, not months);
- no hostile or legal response to good-faith research;
- public credit only if you want it.

If you need a paid engagement, SLA-backed response, or an audited system,
this is not that. The contract is small on purpose; an independent review of
`contracts/covenant/src/lib.rs` is a bounded task.

## Design context for reviewers

The trust model is documented honestly in
[docs/architecture.md](docs/architecture.md) (trust model section): Covenant
makes false history expensive and verifiable, not impossible. Known
accepted-for-v1 limitations include the centralized resolver (deploy-time
address) and self-reported offchain fills; these are design choices, not
vulnerabilities, though arguments that they are worse than documented are in
scope.
