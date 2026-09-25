/**
 * DEMO indexer: serves the exact JSON API the web app reads (indexer/src/server.ts)
 * but from synthetic, generated data. Exists ONLY so the UI can be reviewed
 * before a contract is deployed. Every response carries "demo": true, and the
 * web app renders a "DEMO DATA" banner whenever it sees that flag.
 *
 * The dataset is deterministic (seeded PRNG, no Math.random) so restarts serve
 * byte-identical data and screenshots stay comparable. Arcs are hand-shaped per
 * strategy: steady climber, volatile uptrend, drawdown-recovery, flat honest,
 * a top-then-fade, a range sawtooth, an honest loser, a high flyer, and an
 * early fraud whose late epochs were invalidated.
 *
 * Start: node indexer/demo-server.mjs   (PORT env, default 8788)
 * This is NOT the real indexer and never touches a chain.
 */

import { createServer } from "node:http";

const PORT = Number(process.env.PORT ?? 8788);

const now = Date.now() / 1000;
const HOUR = 3600;
const EPOCH_SPACING = 4 * HOUR;

/** Deterministic PRNG (mulberry32): same seed, same series, every run. */
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const round6 = (x) => x.toFixed(6);

const hexRoot = (s) =>
  "0x" + Buffer.from(s.padEnd(28, "demo").slice(0, 28)).toString("hex").padEnd(64, "0");

/**
 * Builds one strategy's epoch history.
 *   spec.seedEquity  epoch 0 equity in human USDG (also its seed netFlow)
 *   spec.count       number of committed epochs
 *   spec.status(i)   epoch status: 0 pending, 1 finalized, 2 challenged, 3 invalidated
 *   spec.drift(i)    expected per-epoch move as a fraction of equity
 *   spec.vol         noise amplitude as a fraction of equity
 */
function buildEpochs(spec) {
  const rand = mulberry32(spec.seed);
  const startTs = now - (spec.count + 2) * EPOCH_SPACING;
  const epochs = [];
  let equity = spec.seedEquity;
  for (let i = 0; i < spec.count; i++) {
    const status = spec.status(i);
    let pnl = null;
    if (i > 0) {
      // one move per epoch; pnl is exactly the equity delta (netFlow is 0)
      const move = spec.drift(i) + (rand() * 2 - 1) * spec.vol;
      pnl = round6(equity * move);
      equity = Math.max(1, equity * (1 + move));
    }
    epochs.push({
      epochIndex: String(i),
      equity: round6(i === 0 ? spec.seedEquity : equity),
      netFlow: i === 0 ? round6(spec.seedEquity) : "0.000000",
      tradesRoot: hexRoot(`${spec.name}-${i}`),
      evidenceUri: `evidence/${spec.name}/epoch-${i}.json`,
      status,
      committedAt: new Date((startTs + i * EPOCH_SPACING) * 1000).toISOString(),
      // PnL exists only on finalized epochs, like the chain: commit -> pnl is
      // set at finalize; pending, challenged, and invalidated carry none.
      pnl: status === 1 ? (i === 0 ? "0.000000" : pnl) : null,
    });
  }
  return epochs;
}

const SPECS = [
  {
    id: "1",
    name: "jev-laya-sol",
    owner: "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
    bond: "10.000000",
    strategyStatus: 0,
    seed: 11,
    seedEquity: 1000,
    count: 32,
    vol: 0.006,
    drift: () => 0.007,
    status: (i) => (i < 30 ? 1 : 0),
  },
  {
    id: "2",
    name: "basis-skew",
    owner: "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
    bond: "25.000000",
    strategyStatus: 0,
    seed: 22,
    seedEquity: 5000,
    count: 28,
    vol: 0.045,
    drift: () => 0.012,
    status: (i) => (i < 24 ? 1 : i < 26 ? 2 : 0), // challenged cluster at 24-25
  },
  {
    id: "3",
    name: "delta-neutral-y",
    owner: "0x90f79bf6eb2c4f8463652b98e2b8b0f8e79a0e8a",
    bond: "20.000000",
    strategyStatus: 0,
    seed: 33,
    seedEquity: 12000,
    count: 26,
    vol: 0.008,
    drift: (i) => (i >= 8 && i <= 13 ? -0.02 : i <= 20 ? 0.012 : 0.002), // drawdown, recovery, calm
    status: (i) => (i < 24 ? 1 : 0),
  },
  {
    id: "4",
    name: "tbill-basis",
    owner: "0xd8da6bf26964af9d7eed9e03e53415d37aa96045",
    bond: "50.000000",
    strategyStatus: 0,
    seed: 44,
    seedEquity: 25000,
    count: 40,
    vol: 0.0002,
    drift: () => 0.0004,
    status: (i) => (i < 38 ? 1 : 0),
  },
  {
    id: "5",
    name: "momentum-alpha",
    owner: "0xa0ee7a142d267c1f36714a4f8a75f654c7a3f0d2",
    bond: "5.000000",
    strategyStatus: 1, // suspended after the upheld challenges
    seed: 55,
    seedEquity: 800,
    count: 12,
    vol: 0.02,
    drift: (i) => (i < 4 ? 0.09 : 0.11), // fake prints that got invalidated
    status: (i) => (i < 4 ? 1 : 3), // invalidated run
  },
  {
    id: "6",
    name: "funding-carry-eth",
    owner: "0x254672c3e5c8fa727b3e7402d04b1c6fd3d2b6b9",
    bond: "15.000000",
    strategyStatus: 0,
    seed: 66,
    seedEquity: 7500,
    count: 36,
    vol: 0.003,
    drift: () => 0.004,
    status: (i) => (i < 34 ? 1 : 0),
  },
  {
    id: "7",
    name: "vol-harvest",
    owner: "0x8e5d8f4c0f1e6d3a9b7c2a5f4e8d1c0b9a8f7e6d",
    bond: "12.000000",
    strategyStatus: 0,
    seed: 77,
    seedEquity: 3000,
    count: 26,
    vol: 0.02,
    drift: (i) => (i < 14 ? 0.016 : -0.004), // climbs, tops, fades
    status: (i) => (i < 24 ? 1 : 0),
  },
  {
    id: "8",
    name: "grid-maker",
    owner: "0x6b175474e89094c44da98b954eedeac495271d0f",
    bond: "8.000000",
    strategyStatus: 0,
    seed: 88,
    seedEquity: 2200,
    count: 30,
    vol: 0.004,
    drift: (i) => (i % 2 === 0 ? -0.006 : 0.008), // range sawtooth
    status: (i) => (i < 28 ? 1 : 0),
  },
  {
    id: "9",
    name: "orb-alpha",
    owner: "0x2b5ad5c4795c026514f8317c7a215e218dccd6cf",
    bond: "40.000000",
    strategyStatus: 0,
    seed: 99,
    seedEquity: 500,
    count: 20,
    vol: 0.05,
    drift: () => 0.028,
    status: (i) => (i < 18 ? 1 : 0),
  },
  {
    id: "10",
    name: "perp-mm-core",
    owner: "0x6813eb9362372eef6200f3b1dbc3f819671cba69",
    bond: "100.000000",
    strategyStatus: 0,
    seed: 110,
    seedEquity: 18000,
    count: 24,
    vol: 0.002,
    drift: () => -0.0015, // honest loser: tight spread capture, slow bleed
    status: (i) => (i < 22 ? 1 : 0),
  },
];

const strategies = SPECS.map((spec) => ({
  id: spec.id,
  owner: spec.owner,
  name: spec.name,
  status: spec.strategyStatus,
  bond: spec.bond,
  epochs: buildEpochs(spec),
}));

/**
 * Mirrors indexer/src/state.ts rederive(): cumulative PnL and headline return
 * come from finalized epochs only; the return base is the first finalized
 * equity so deposits do not inflate it.
 */
function derive(s) {
  const fin = s.epochs
    .filter((e) => e.status === 1)
    .sort((a, b) => Number(a.epochIndex) - Number(b.epochIndex));
  const cumulativePnl = fin.reduce((acc, e) => acc + Number(e.pnl ?? 0), 0);
  const base = fin.length > 0 ? Number(fin[0].equity) : 0;
  let returnWad = null;
  if (base !== 0) {
    returnWad = String(Math.round((cumulativePnl / base) * 1e18));
  }
  const count = (st) => s.epochs.filter((e) => e.status === st).length;
  return {
    equity: fin.length > 0 ? fin[fin.length - 1].equity : null,
    cumulativePnl: round6(cumulativePnl),
    returnWad,
    finalizedEpochs: count(1),
    pendingEpochs: count(0),
    challengedEpochs: count(2),
    invalidatedEpochs: count(3),
  };
}

/**
 * Mirrors indexer/src/server.ts sparkSeries(): finalized epochs only, ascending
 * epoch order, last 32 points, plain human numbers (demo equities are already
 * human strings, the real indexer converts bigint base units / 1e6); null below
 * 2 finalized checkpoints.
 */
function sparkOf(epochs) {
  const points = epochs
    .filter((e) => e.status === 1 && e.equity !== null)
    .sort((a, b) => Number(a.epochIndex) - Number(b.epochIndex))
    .slice(-32)
    .map((e) => Number(e.equity));
  return points.length >= 2 ? points : null;
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
    spark: sparkOf(s.epochs),
  };
}

const server = createServer((req, res) => {
  res.setHeader("Content-Type", "application/json");
  const url = new URL(req.url, "http://localhost");
  console.error(`[demo] ${req.method} ${url.pathname}`);
  if (url.pathname === "/health") {
    res.end(JSON.stringify({ demo: true, ok: true, lastBlock: "0" }));
    return;
  }
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
    res.end(JSON.stringify({ demo: true, ...leaderboardRow(s), epochs: s.epochs }));
    return;
  }
  res.statusCode = 404;
  res.end(JSON.stringify({ demo: true, error: "unknown route" }));
});

server.listen(PORT, "127.0.0.1", () => {
  console.error(`DEMO indexer on http://127.0.0.1:${PORT} - synthetic data, NOT live onchain`);
});
