#!/bin/bash
# SHOT 5: finalize after the 5-minute window elapsed.
set -e
cd "$(dirname "$0")/../.."
export PATH="$HOME/.foundry/bin:$PATH"
RPC=https://arbitrum-sepolia.publicnode.com
COV=0x1de6ccb02f29308851a9f59c09845c6d348d16a4
PK=$(grep DEPLOYER_PRIVATE_KEY ~/.config/covenant-deploy/deployer.env | cut -d= -f2)
echo '{"action":"finalize","strategyId":"2","epochIndex":"0"}' \
  | COVENANT_RPC_URL=$RPC COVENANT_CONTRACT_ADDRESS=$COV COVENANT_PRIVATE_KEY=$PK \
  node vouch/vouch/attest/bridge.mjs
