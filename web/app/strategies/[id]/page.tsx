import Link from "next/link";
import {
  checkpointPill,
  fetchStrategy,
  formatReturnWad,
  formatUsdg,
  statusPill,
  type EpochRow,
  type StrategyDetail,
} from "../../../lib/api";

export const dynamic = "force-dynamic";

/** "2h ago" style relative time; the absolute timestamp rides on the title. */
function relTime(iso: string | null): string {
  if (iso === null) return "-";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function PnlCell({ pnl }: { pnl: string | null }) {
  if (pnl === null) return <span className="dim">-</span>;
  const value = Number(pnl);
  if (value === 0) return <span className="dim">{formatUsdg(pnl)}</span>;
  const negative = pnl.startsWith("-");
  const signed = `${negative ? "" : "+"}${formatUsdg(pnl)}`;
  return <span className={negative ? "neg" : "pos"}>{signed}</span>;
}

function epochRowClass(status: number): string {
  if (status === 0 || status === 2) return "row-warn"; // pending / challenged
  if (status >= 3) return "row-bad"; // invalidated
  return "";
}

export default async function StrategyPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let detail: StrategyDetail;
  try {
    detail = await fetchStrategy(id);
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return (
      <>
        <div className="page-head">
          <h1>Strategy {id}</h1>
        </div>
        <div className="error-panel">
          NOT INDEXED ({message})
          <br />
          Fix: run <code>RPC_URL=… CONTRACT_ADDRESS=… pnpm start</code> in{" "}
          <code>indexer/</code>.
        </div>
      </>
    );
  }

  const sp = statusPill(detail.status);
  const retWad = detail.derived.returnWad;
  const ret =
    retWad === null ? null : retWad === "0" ? "0.00%" : formatReturnWad(retWad);
  const retCls =
    retWad === null || retWad === "0"
      ? "dim"
      : retWad.startsWith("-")
        ? "neg"
        : "pos";
  const d = detail.derived;

  return (
    <>
      <div className="page-head">
        <h1>
          {detail.name || `strategy-${detail.id}`}{" "}
          <span className={`badge ${sp.cls}`}>{sp.label}</span>
        </h1>
        <p className="lede mono">
          owner {detail.owner} - bond {formatUsdg(detail.bond)} USDG (slashed to
          a successful challenger on proven misreporting)
        </p>
      </div>

      <div className="stat-row">
        <div className="stat">
          <div className="stat-label">Equity (USDG)</div>
          <div className="stat-value">{formatUsdg(d.equity)}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Cumulative PnL (USDG)</div>
          <div className="stat-value">
            {d.cumulativePnl.startsWith("-") || Number(d.cumulativePnl) === 0 ? (
              formatUsdg(d.cumulativePnl)
            ) : (
              <span className="pos">+{formatUsdg(d.cumulativePnl)}</span>
            )}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Return</div>
          <div className={`stat-value ${retCls}`}>{ret ?? "no history"}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Epochs finalized</div>
          <div className="stat-value">
            {d.finalizedEpochs}
            <span className="dim"> / {detail.totalEpochs}</span>
          </div>
        </div>
      </div>

      <p className="table-foot">
        pending {d.pendingEpochs} - challenged {d.challengedEpochs} -
        invalidated {d.invalidatedEpochs}
      </p>

      <h2>Epochs (finalized checkpoints only are included in returns)</h2>
      {detail.epochs.length === 0 ? (
        <div className="empty-state">
          NO EPOCHS COMMITTED YET
          <br />
          This strategy has not committed its first equity checkpoint.
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Epoch</th>
              <th>Committed</th>
              <th className="num">Equity (USDG)</th>
              <th className="num">Net flow (USDG)</th>
              <th className="num">PnL (USDG)</th>
              <th>Status</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {detail.epochs.map((e: EpochRow) => {
              const pill = checkpointPill(e.status);
              return (
                <tr
                  key={e.epochIndex}
                  className={epochRowClass(e.status)}
                >
                  <td className="mono">#{e.epochIndex}</td>
                  <td className="mono dim" title={e.committedAt ?? undefined}>
                    {relTime(e.committedAt)}
                  </td>
                  <td className="num">{formatUsdg(e.equity)}</td>
                  <td className="num">{formatUsdg(e.netFlow)}</td>
                  <td className="num">
                    <PnlCell pnl={e.pnl} />
                  </td>
                  <td>
                    <span className={`badge ${pill.cls}`}>{pill.label}</span>
                  </td>
                  <td>
                    {e.evidenceUri === null ? (
                      <span className="dim">-</span>
                    ) : (
                      <span className="mono dim truncate" title={e.evidenceUri}>
                        {e.evidenceUri}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <h2>Audit a fill</h2>
      <p className="lede">
        Any disclosed trade receipt can be checked against the committed epoch
        root. Take the receipt hash and Merkle proof from the strategy&apos;s
        evidence bundle, then paste them on the{" "}
        <Link href="/verify" className="text-link">
          verification page
        </Link>
        .
      </p>
    </>
  );
}
