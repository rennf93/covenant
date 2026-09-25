import Link from "next/link";
import { fetchDemoFlag, fetchLeaderboard, formatReturnWad, formatUsdg, statusPill, type LeaderboardRow } from "../lib/api";

export const dynamic = "force-dynamic";

function Rank({ i }: { i: number }) {
  return <span className="muted">#{i + 1}</span>;
}

function ReturnCell({ row }: { row: LeaderboardRow }) {
  const wad = row.derived.returnWad;
  if (wad === null) return <span className="muted">no history</span>;
  const positive = !wad.startsWith("-") && wad !== "0";
  return <span className={positive ? "pos" : "neg"}>{formatReturnWad(wad)}</span>;
}

export default async function LeaderboardPage() {
  let rows: LeaderboardRow[] | null = null;
  let error: string | null = null;
  let demo = false;
  try {
    rows = await fetchLeaderboard();
    demo = await fetchDemoFlag();
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <>
      {demo && (
        <div
          className="error-panel"
          style={{ borderColor: "#b8860b", color: "#b8860b" }}
        >
          DEMO DATA - synthetic indexer, not live onchain. This is a UI preview
          before contract deployment; every number here is fabricated.
        </div>
      )}
      <h1>Strategy leaderboard</h1>
      <p className="lede">
        Every strategy commits daily equity checkpoints and a Merkle root of its
        trade receipts to Arbitrum. Returns shown are computed from finalized
        checkpoints only. Pending epochs are unverified until the challenge
        window closes.
      </p>

      {error !== null && (
        <div className="error-panel">
          Indexer unreachable ({error}). Start it with{" "}
          <code>RPC_URL=… CONTRACT_ADDRESS=… pnpm start</code> in{" "}
          <code>indexer/</code>.
        </div>
      )}

      {rows !== null && rows.length === 0 && (
        <p className="muted">
          No strategies registered yet. Be the first: register with a USDG bond
          and start committing epochs.
        </p>
      )}

      {rows !== null && rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Strategy</th>
              <th>Status</th>
              <th>Return</th>
              <th>Cumulative PnL (USDG)</th>
              <th>Equity (USDG)</th>
              <th>Bond (USDG)</th>
              <th>Epochs</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const pill = statusPill(row.status);
              return (
                <tr key={row.id}>
                  <td>
                    <Rank i={i} />
                  </td>
                  <td>
                    <Link href={`/strategies/${row.id}`} className="row-link">
                      {row.name || `strategy-${row.id}`}
                    </Link>
                  </td>
                  <td>
                    <span className={`pill ${pill.cls}`}>{pill.label}</span>
                  </td>
                  <td>
                    <ReturnCell row={row} />
                  </td>
                  <td>{formatUsdg(row.derived.cumulativePnl)}</td>
                  <td>{formatUsdg(row.derived.equity)}</td>
                  <td>{formatUsdg(row.bond)}</td>
                  <td>
                    {row.finalizedEpochs} fin / {row.totalEpochs} total
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}
