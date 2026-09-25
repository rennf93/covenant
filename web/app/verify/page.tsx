import VerifyClient from "./verify-client";

export const dynamic = "force-static";

export default function VerifyPage() {
  return (
    <>
      <div className="page-head">
        <h1>Verify a receipt</h1>
        <p className="lede">
          This page reads the Covenant contract directly from your browser via an
          Arbitrum RPC. No backend, no trust in us: the verification result
          comes from the chain.
        </p>
      </div>
      <VerifyClient />
    </>
  );
}
