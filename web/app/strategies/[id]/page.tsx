import Link from "next/link";
import {
  checkpointPill,
  fetchStrategy,
  formatReturnWad,
  formatUsdg,
  statusPill,
  type StrategyDetail,
} from "../../../lib/api";

export const dynamic = "force-dynamic";

export default async function StrategyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let detail: StrategyDetail;
  try {
    detail = await fetchStrategy(id);
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return (
      <>
        <h1>Strategy {id}</h1>
        <div className="error-panel">Not indexed ({message}).</div>
      </>
    );
  }

  const sp = statusPill(detail.status);
  const ret = formatReturnWad(detail.derived.returnWad);
  const retCls = detail.derived.returnWad !== null && !detail.derived.returnWad.startsWith("-") ? "pos" : "neg";

  return (
    <>
      <h1>
        {detail.name || `strategy-${detail.id}`}{" "}
        <span className={`pill ${sp.cls}`}>{sp.label}</span>
      </h1>
      <p className="lede">
        Owned by <code>{detail.owner}</code>. Bond: {formatUsdg(detail.bond)} USDG
        (slashed to a successful challenger on proven misreporting).
      </p>

      <h2>Performance (finalized epochs only)</h2>
      <dl className="kv">
        <dt>Return</dt>
        <dd className={retCls}>{ret}</dd>
        <dt>Cumulative PnL</dt>
        <dd>{formatUsdg(detail.derived.cumulativePnl)} USDG</dd>
        <dt>Current equity</dt>
        <dd>{formatUsdg(detail.derived.equity)} USDG</dd>
        <dt>Epochs finalized</dt>
        <dd>
          {detail.derived.finalizedEpochs} / {detail.totalEpochs} committed
        </dd>
        <dt>Pending / challenged / invalidated</dt>
        <dd>
          {detail.derived.pendingEpochs} / {detail.derived.challengedEpochs} /{" "}
          {detail.derived.invalidatedEpochs}
        </dd>
      </dl>

      <h2>Epochs</h2>
      <table>
        <thead>
          <tr>
            <th>Epoch</th>
            <th>Status</th>
            <th>Equity (USDG)</th>
            <th>Net flow (USDG)</th>
            <th>PnL (USDG)</th>
            <th>Committed at</th>
            <th>Receipts root</th>
          </tr>
        </thead>
        <tbody>
          {detail.epochs.map((e) => {
            const pill = checkpointPill(e.status);
            return (
              <tr key={e.epochIndex}>
                <td>{e.epochIndex}</td>
                <td>
                  <span className={`pill ${pill.cls}`}>{pill.label}</span>
                </td>
                <td>{formatUsdg(e.equity)}</td>
                <td>{formatUsdg(e.netFlow)}</td>
                <td>{e.pnl === null ? "-" : formatUsdg(e.pnl)}</td>
                <td>{e.committedAt}</td>
                <td className="muted">{e.tradesRoot?.slice(0, 18)}…</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <h2>Audit a fill</h2>
      <p className="lede">
        Any disclosed trade receipt can be checked against the committed epoch
        root. Take the receipt hash and Merkle proof from the strategy&apos;s
        evidence bundle, then paste them on the{" "}
        <Link href="/verify">verification page</Link>.
      </p>
    </>
  );
}
