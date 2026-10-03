#!/bin/bash
# SHOT 5b: read back the numbers (equity 10.5, HWM 10.5, PnL +5.5).
set -e
cd "$(dirname "$0")/../.."
export PATH="$HOME/.foundry/bin:$PATH"
RPC=https://arbitrum-sepolia.publicnode.com
COV=0x1de6ccb02f29308851a9f59c09845c6d348d16a4
echo '{"action":"getPerformance","strategyId":"2"}' \
  | COVENANT_RPC_URL=$RPC COVENANT_CONTRACT_ADDRESS=$COV \
  node vouch/vouch/attest/bridge.mjs
