# The vouch agent. laya (System-1) is installed from PyPI; the System-2
# rewriter needs any OpenAI-compatible endpoint (VOUCH_S2_BASE_URL) and is
# optional: without it epochs simply run without rule rewrites.
FROM python:3.12-slim
WORKDIR /app

# node is required by the attest bridge (vouch/attest/bridge.mjs shells out
# to node + viem): without it only ledger-only attestation works. Debian
# slim ships an LTS node; the bridge uses plain ESM + viem, no build step.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir laya httpx pydantic-settings

COPY vouch/ vouch/
# Onchain (commit/finalize/register/sign) mode additionally needs viem, which
# the bridge resolves from a covenant/sdk tree SIBLING of /app/vouch. Mount
# or copy one in to turn the chain path on (see README, "Onchain mode in
# docker"); ledger-only bundles, receipts and signatures fallback need none
# of it, which is the container default.
ENV VOUCH_S1_PROVIDER=local
ENV COVENANT_ATTEST=1

# Attested shadow mode: live prices, paper money, receipts + evidence on the
# mounted volume. Real-money mode stays out of the container on purpose: a
# dashboard click must never be able to place a real order.
# WORKDIR inside the copied project dir so run_shadow.py and the installed
# vouch package resolve (the package now lives at vouch/vouch/).
WORKDIR /app/vouch
CMD ["python", "run_shadow.py", "--attest", "--forever"]
