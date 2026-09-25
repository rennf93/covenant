/**
 * DEMO indexer: serves the exact JSON API the web app reads (indexer/src/server.ts)
 * but from synthetic, hand-written data. Exists ONLY so the UI can be reviewed
 * before a contract is deployed. Every response carries "demo": true, and the
 * web app renders a "DEMO DATA" banner whenever it sees that flag.
 *
 * Start: node indexer/demo-server.mjs   (PORT env, default 8788)
 * This is NOT the real indexer and never touches a chain.
 */

import { createServer } from "node:http";

const PORT = Number(process.env.PORT ?? 8788);

const now = Date.now() / 1000;
const HOUR = 3600;

// Equity curves in human USDG terms (6-decimal strings); flat-to-slightly-up
// for the honest demo strategy, a challenge mid-flight, and a suspended fraud.
const jevEpochs = [
  { equity: "1000.000000", netFlow: "10.000000", pnl: null },
  { equity: "1000.421700", netFlow: "0.000000", pnl: "0.421700" },
  { equity: "999.804200", netFlow: "0.000000", pnl: "-0.617500" },
  { equity: "1001.193400", netFlow: "0.000000", pnl: "1.389200" },
  { equity: "1001.011800", netFlow: "0.000000", pnl: "-0.181600" },
  { equity: "1001.876500", netFlow: "0.000000", pnl: "0.864700" },
];
const neutralEpochs = [
  { equity: "500.000000", netFlow: "5.000000", pnl: null },
  { equity: "500.102400", netFlow: "0.000000", pnl: "0.102400" },
  { equity: "500.296100", netFlow: "0.000000", pnl: "0.193700" },
  { equity: "500.151900", netFlow: "0.000000", pnl: "-0.144200" },
];
const momentumEpochs = [
  { equity: "250.000000", netFlow: "2.500000", pnl: null },
  { equity: "262.401900", netFlow: "0.000000", pnl: "12.401900" },
  { equity: "244.117500", netFlow: "0.000000", pnl: "-18.284400" },
  { equity: "231.002200", netFlow: "0.000000", pnl: "-13.115300" },
];

const hexRoot = (s) =>
  "0x" + Buffer.from(s.padEnd(28, "demo").slice(0, 28)).toString("hex").padEnd(64, "0");

function buildEpochs(startIdx, specs, startTs, statuses, evidencePrefix) {
  return specs.map((e, i) => ({
    epochIndex: String(startIdx + i),
    equity: e.equity,
    netFlow: e.netFlow,
    tradesRoot: specs[i].pnl === null && i === 0 ? "0x0000000000000000000000000000000000000000000000000000000000000000" : hexRoot(evidencePrefix + i),
    evidenceUri: `evidence/${evidencePrefix}/epoch-${startIdx + i}.json`,
    status: statuses[i],
    committedAt: new Date((startTs + i * 4 * HOUR) * 1000).toISOString(),
    pnl: e.pnl,
  }));
}

const strategies = [
  {
    id: "1",
    owner: "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
    name: "jev-laya-sol",
    status: 0,
    bond: "10.000000",
    epochs: buildEpochs(0, jevEpochs, now - 26 * HOUR,
      [1, 1, 1, 1, 1, 0], "jev-laya-sol"),
  },
  {
    id: "2",
    owner: "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
    name: "market-neutral-sol",
    status: 0,
    bond: "10.000000",
    epochs: buildEpochs(0, neutralEpochs, now - 20 * HOUR,
      [1, 1, 1, 2], "market-neutral-sol"),
  },
  {
    id: "3",
    owner: "0x90f79bf6eb2c4f8463652b98e2b8b0f8e79a0e8a",
    name: "momentum-alpha",
    status: 1,
    bond: "0.000000",
    epochs: buildEpochs(0, momentumEpochs, now - 40 * HOUR,
      [1, 1, 3, 3], "momentum-alpha"),
  },
];

function derive(s) {
  const fin = s.epochs.filter((e) => e.status === 1);
  const last = fin.length ? fin[fin.length - 1].equity : null;
  const firstEquity = s.epochs[0].equity;
  const firstFlow = s.epochs[0].netFlow;
  const cumulativePnl = fin.reduce((acc, e) => acc + Number(e.pnl ?? 0), 0);
  let returnWad = null;
  if (fin.length > 0) {
    const invested = Number(firstEquity);
    const ret = cumulativePnl / invested;
    returnWad = String(Math.round(ret * 1e18));
  }
  const count = (st) => s.epochs.filter((e) => e.status === st).length;
  return {
    equity: last,
    cumulativePnl: String(cumulativePnl.toFixed(6)),
    returnWad,
    finalizedEpochs: count(1),
    pendingEpochs: count(0),
    challengedEpochs: count(2),
    invalidatedEpochs: count(3),
  };
}

function leaderboardRow(s) {
  const derived = derive(s);
  return {
    id: s.id,
    owner: s.owner,
    name: s.name,
    status: s.status,
    bond: s.bond,
    derived,
    finalizedEpochs: derived.finalizedEpochs,
    totalEpochs: s.epochs.length,
  };
}

const server = createServer((req, res) => {
  res.setHeader("Content-Type", "application/json");
  const url = new URL(req.url, "http://localhost");
  console.error(`[demo] ${req.method} ${url.pathname}`);
  if (url.pathname === "/strategies") {
    res.end(JSON.stringify({ demo: true, rows: strategies.map(leaderboardRow) }));
    return;
  }
  const m = url.pathname.match(/^\/strategies\/(\d+)$/);
  if (m) {
    const s = strategies.find((x) => x.id === m[1]);
    if (!s) {
      res.statusCode = 404;
      res.end(JSON.stringify({ demo: true, error: "not found" }));
      return;
    }
    const derived = derive(s);
    res.end(JSON.stringify({ demo: true, ...leaderboardRow(s), derived, epochs: s.epochs }));
    return;
  }
  res.statusCode = 404;
  res.end(JSON.stringify({ demo: true, error: "unknown route" }));
});

server.listen(PORT, "127.0.0.1", () => {
  console.error(`DEMO indexer on http://127.0.0.1:${PORT} - synthetic data, NOT live onchain`);
});
