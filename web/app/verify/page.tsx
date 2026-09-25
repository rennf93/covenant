import EvidenceVerify from "../../components/evidence-verify";
import VerifyTabs from "../../components/verify-tabs";
import VerifyClient from "./verify-client";

// Dynamic, not prerendered: the header's live indexer chip is fetched per
// request, and a build-time snapshot bakes a stale "indexer down" into the page.
export const dynamic = "force-dynamic";

/** The three honest steps a browser-side receipt verification walks through. */
const RECEIPT_STEPS = [
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

/** The three honest steps a browser-side evidence bundle check walks through. */
const EVIDENCE_STEPS = [
  {
    title: "Load the signed bundle",
    copy: "Fetch a bundle by URL or paste its JSON. The bundle carries the epoch commitment fields, the receipts with their Merkle proofs, and the producer's 65-byte signature.",
  },
  {
    title: "Check every receipt locally",
    copy: "Each receipt's canonical leaf hash is recomputed with the exact contract encoding, then its proof is walked against the bundle's tradesRoot. One wrong field breaks a leaf.",
  },
  {
    title: "Recover the signer",
    copy: "The EIP-712 message is rebuilt exactly as the producer signed it, the address is recovered from the signature, and it must be the strategy owner the contract reports.",
  },
];

function Steps({ steps }: { steps: typeof RECEIPT_STEPS }) {
  return (
    <aside className="steps" aria-label="How verification works">
      {steps.map((s, i) => (
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
  );
}

export default function VerifyPage() {
  return (
    <>
      <div className="page-head reveal" style={{ "--reveal-i": 0 } as React.CSSProperties}>
        <p className="eyebrow eyebrow-live">Trustless audit</p>
        <h1 className="display-hero">Verify without trusting us</h1>
        <p className="lede">
          This page reads the Covenant contract directly from your browser via an
          Arbitrum RPC, and checks evidence bundles locally in your browser. No
          backend, no trust in us: every verdict is computed on your machine.
        </p>
      </div>
      <VerifyTabs
        receiptLabel="Verify a receipt"
        evidenceLabel="Verify an evidence bundle"
        receipt={
          <>
            <Steps steps={RECEIPT_STEPS} />
            <div className="panel reveal" style={{ "--reveal-i": 2 } as React.CSSProperties}>
              <div className="panel-core">
                <div className="panel-head">
                  <span>Receipt check</span>
                  <span className="panel-meta">client-side via viem</span>
                </div>
                <VerifyClient />
              </div>
            </div>
          </>
        }
        evidence={
          <>
            <Steps steps={EVIDENCE_STEPS} />
            <div className="panel reveal" style={{ "--reveal-i": 2 } as React.CSSProperties}>
              <div className="panel-core">
                <div className="panel-head">
                  <span>Evidence bundle check</span>
                  <span className="panel-meta">merkle + EIP-712, in your browser</span>
                </div>
                <EvidenceVerify />
              </div>
            </div>
          </>
        }
      />
    </>
  );
}
