import Link from "next/link";
import {
  fetchDemoFlag,
  fetchLeaderboard,
  formatReturnWad,
  formatUsdg,
  statusPill,
  type LeaderboardRow,
} from "../lib/api";

export const dynamic = "force-dynamic";

function Rank({ i }: { i: number }) {
  return <span className="mono dim">#{i + 1}</span>;
}

function ReturnCell({ row }: { row: LeaderboardRow }) {
  const wad = row.derived.returnWad;
  if (wad === null) return <span className="dim">no history</span>;
  if (wad === "0") return <span className="dim">0.00%</span>;
  const positive = !wad.startsWith("-");
  return (
    <span className={positive ? "pos" : "neg"}>{formatReturnWad(wad)}</span>
  );
}

function EpochsCell({ row }: { row: LeaderboardRow }) {
  return (
    <span className="mono">
      {row.finalizedEpochs} fin / {row.totalEpochs} total
      {row.derived.pendingEpochs > 0 && (
        <span className="warn"> +{row.derived.pendingEpochs} pending</span>
      )}
    </span>
  );
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
        <div className="banner-warn">
          DEMO DATA - synthetic indexer, not live onchain. This is a UI preview
          before contract deployment; every number here is fabricated.
        </div>
      )}

      <div className="page-head">
        <h1>Strategy leaderboard</h1>
        <p className="lede">
          Every strategy commits daily equity checkpoints and a Merkle root of
          its trade receipts to Arbitrum. Returns shown are computed from
          finalized checkpoints only. Pending epochs are unverified until the
          challenge window closes.
        </p>
      </div>

      {error !== null && (
        <div className="error-panel">
          INDEXER UNREACHABLE ({error})
          <br />
          Fix: run{" "}
          <code>RPC_URL=… CONTRACT_ADDRESS=… pnpm start</code> in{" "}
          <code>indexer/</code>.
        </div>
      )}

      {rows !== null && rows.length === 0 && (
        <div className="empty-state">
          NO STRATEGIES REGISTERED
          <br />
          Be the first: register with a USDG bond and start committing epochs.
        </div>
      )}

      {rows !== null && rows.length > 0 && (
        <>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Strategy</th>
                <th>Status</th>
                <th className="num">Return</th>
                <th className="num">Cumulative PnL (USDG)</th>
                <th className="num">Equity (USDG)</th>
                <th className="num">Bond (USDG)</th>
                <th className="num">Epochs</th>
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
                      <Link
                        href={`/strategies/${row.id}`}
                        className="row-link"
                      >
                        {row.name || `strategy-${row.id}`}
                      </Link>
                      <div className="sub">{row.owner}</div>
                    </td>
                    <td>
                      <span className={`badge ${pill.cls}`}>{pill.label}</span>
                    </td>
                    <td className="num">
                      <ReturnCell row={row} />
                    </td>
                    <td className="num">
                      {formatUsdg(row.derived.cumulativePnl)}
                    </td>
                    <td className="num">{formatUsdg(row.derived.equity)}</td>
                    <td className="num">{formatUsdg(row.bond)}</td>
                    <td className="num">
                      <EpochsCell row={row} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="table-foot">
            Returns are computed from finalized checkpoints only. Challenge a
            pending epoch by staking USDG.
          </p>
        </>
      )}
    </>
  );
}
