/**
 * Builds the demo epoch-0 commit package for strategy 2 ("vouch-sol-demo"):
 * five canonical receipts from real recent SOL-USD prices (Coinbase, fetched
 * live), the Merkle root, and one receipt + proof for the browser verifier.
 * Receipts are demo paper fills; the hashing and tree are the real SDK
 * primitives the contract enforces. Output: demo-commit.json.
 */
import { createRequire } from "node:module";
const req = createRequire(new URL("../../sdk/package.json", import.meta.url));
const sdk = await import(new URL("../../sdk/dist/src/index.js", import.meta.url).href);
const { Venue, Side, makeReceipt, EpochBuilder } = sdk;

const r = await fetch("https://api.exchange.coinbase.com/products/SOL-USD/candles?granularity=60");
const rows = await r.json();
const closes = rows.slice(0, 5).map((x) => x[4]).reverse();
const now = Math.floor(Date.now() / 1000) - 300;
const fills = closes.map((close, i) => ({
  orderId: `demo-sol-${now}-${i}`,
  side: i % 2 === 0 ? Side.Buy : Side.Sell,
  size: (0.2 + 0.1 * (i % 3)).toFixed(3),
  price: close.toFixed(2),
  fee: (Number(close) * Number((0.2 + 0.1 * (i % 3)).toFixed(3)) * 0.003).toFixed(4),
  filledAt: now + i * 30,
}));

const STRATEGY_ID = 2n; // sequential: strategyCount is 1 on the demo covenant
const b = new EpochBuilder(STRATEGY_ID, 0n);
fills.forEach((f, i) =>
  b.add(
    makeReceipt({
      strategyId: STRATEGY_ID,
      epochIndex: 0n,
      venue: Venue.Coinbase,
      venueOrderId: f.orderId,
      instrument: "SOL-USD",
      side: f.side,
      sizeBase: f.size,
      price: f.price,
      fee: f.fee,
      filledAt: f.filledAt,
    }),
  ),
);
const payload = b.commitPayload({
  equityUsdg: 10500000n, // 10.5 USDG ending equity (6 decimals)
  netFlowUsdg: 5000000n, // +5 USDG seed flow (epoch 0 carries seed capital)
  evidenceURI: "https://github.com/rennf93/covenant/tree/main/submission-video/demo",
});
const proofIdx = 2;
const leaves = payload.receipts;
const out = {
  builtAt: new Date().toISOString(),
  strategyId: "2",
  strategyName: "vouch-sol-demo",
  epochIndex: "0",
  equity: "10500000",
  netFlow: "5000000",
  tradesRoot: payload.tradesRoot,
  evidenceURI: payload.evidenceURI,
  fills,
  receipts: leaves,
  verifyOne: {
    index: proofIdx,
    receiptHash: leaves[proofIdx],
    proof: payload.tree.proof(proofIdx),
  },
};
const { writeFileSync } = await import("node:fs");
writeFileSync(new URL("./demo-commit.json", import.meta.url), JSON.stringify(out, null, 2) + "\n");
console.log("tradesRoot:", payload.tradesRoot);
console.log("receipts:", leaves.length, "verify leaf idx:", proofIdx);
