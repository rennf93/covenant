# The vouch agent. laya (System-1) is installed from PyPI; the System-2
# rewriter needs any OpenAI-compatible endpoint (VOUCH_S2_BASE_URL) and is
# optional: without it epochs simply run without rule rewrites.
FROM python:3.12-slim
WORKDIR /app

RUN pip install --no-cache-dir laya httpx

COPY vouch/ vouch/
ENV VOUCH_S1_PROVIDER=local
ENV COVENANT_ATTEST=1

# Attested shadow mode: live prices, paper money, receipts + evidence on the
# mounted volume. Real-money mode stays out of the container on purpose: a
# dashboard click must never be able to place a real order.
CMD ["python", "run_shadow.py", "--attest", "--forever"]
