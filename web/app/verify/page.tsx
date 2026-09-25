import VerifyClient from "./verify-client";

// Dynamic, not prerendered: the header's live indexer chip is fetched per
// request, and a build-time snapshot bakes a stale "indexer down" into the page.
export const dynamic = "force-dynamic";

/** The three honest steps a browser-side receipt verification walks through. */
const STEPS = [
  {
    title: "Load the epoch root",
    copy: "Your browser reads the committed tradesRoot for the strategy's epoch straight from the Covenant contract on Arbitrum. No indexer, no API key, no trust in this site.",
  },
  {
    title: "Walk the Merkle proof",
    copy: "Each sibling hash from the evidence bundle folds your receipt hash one level up. A single wrong leaf, wrong order, or missing sibling lands on a different root.",
  },
  {
    title: "Leaf matches onchain",
    copy: "The contract's verifyReceipt returns true only when the computed root equals the committed one. The verdict you see comes from the chain, not from us.",
  },
];

export default function VerifyPage() {
  return (
    <>
      <div className="page-head reveal" style={{ "--reveal-i": 0 } as React.CSSProperties}>
        <p className="eyebrow eyebrow-live">Trustless audit</p>
        <h1 className="display-hero">Verify a receipt</h1>
        <p className="lede">
          This page reads the Covenant contract directly from your browser via an
          Arbitrum RPC. No backend, no trust in us: the verification result
          comes from the chain.
        </p>
      </div>
      <div className="verify-split">
        <aside className="steps" aria-label="How verification works">
          {STEPS.map((s, i) => (
            <div
              key={s.title}
              className="step reveal"
              style={{ "--reveal-i": i + 1 } as React.CSSProperties}
            >
              <span className="step-num" aria-hidden="true">
                {i + 1}
              </span>
              <div>
                <p className="step-title">{s.title}</p>
                <p className="step-copy">{s.copy}</p>
              </div>
            </div>
          ))}
        </aside>
        <div
          className="panel reveal"
          style={{ "--reveal-i": 2 } as React.CSSProperties}
        >
          <div className="panel-core">
            <div className="panel-head">
              <span>Receipt check</span>
              <span className="panel-meta">client-side via viem</span>
            </div>
            <VerifyClient />
          </div>
        </div>
      </div>
    </>
  );
}
