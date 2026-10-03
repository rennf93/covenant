#!/bin/bash
# SHOT 1: register the strategy, bond pulled into escrow on camera.
set -e
cd "$(dirname "$0")/../.."
export PATH="$HOME/.foundry/bin:$PATH"
RPC=https://arbitrum-sepolia.publicnode.com
COV=0x1de6ccb02f29308851a9f59c09845c6d348d16a4
PK=$(grep DEPLOYER_PRIVATE_KEY ~/.config/covenant-deploy/deployer.env | cut -d= -f2)
echo '{"action":"register","name":"vouch-sol-demo","metadataUri":"https://github.com/rennf93/covenant"}' \
  | COVENANT_RPC_URL=$RPC COVENANT_CONTRACT_ADDRESS=$COV COVENANT_PRIVATE_KEY=$PK \
  node vouch/vouch/attest/bridge.mjs
