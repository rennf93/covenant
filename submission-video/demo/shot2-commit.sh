#!/bin/bash
# SHOT 2: commit epoch 0. tradesRoot is already filled in (5 real receipts).
set -e
cd "$(dirname "$0")/../.."
export PATH="$HOME/.foundry/bin:$PATH"
RPC=https://arbitrum-sepolia.publicnode.com
COV=0x1de6ccb02f29308851a9f59c09845c6d348d16a4
PK=$(grep DEPLOYER_PRIVATE_KEY ~/.config/covenant-deploy/deployer.env | cut -d= -f2)
echo '{"action":"commit","strategyId":"2","epochIndex":"0","equity":"10500000","netFlow":"5000000","tradesRoot":"0xfd38304e81c85a8606c61956372d3f4fc8f846582de73c39fb785a1b71de98b7","evidenceUri":"https://github.com/rennf93/covenant/tree/main/submission-video/demo"}' \
  | COVENANT_RPC_URL=$RPC COVENANT_CONTRACT_ADDRESS=$COV COVENANT_PRIVATE_KEY=$PK \
  node vouch/vouch/attest/bridge.mjs
