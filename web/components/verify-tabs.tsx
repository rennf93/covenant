"use client";

import { useId, useState, type ReactNode } from "react";

/**
 * Segmented-control tabs for the verify page: keyboard-reachable buttons with
 * proper tablist/tab/tabpanel semantics; inactive panels stay unmounted so
 * neither form fights the other for state.
 */
export default function VerifyTabs({
  receiptLabel,
  evidenceLabel,
  receipt,
  evidence,
}: {
  receiptLabel: string;
  evidenceLabel: string;
  receipt: ReactNode;
  evidence: ReactNode;
}) {
  const [tab, setTab] = useState<"receipt" | "evidence">("receipt");
  const baseId = useId();

  return (
    <>
      <div className="seg verify-seg" role="tablist" aria-label="Verification mode">
        <button
          type="button"
          role="tab"
          id={`${baseId}-tab-receipt`}
          aria-selected={tab === "receipt"}
          aria-controls={`${baseId}-panel-receipt`}
          className={`seg-item ${tab === "receipt" ? "on" : ""}`}
          onClick={() => setTab("receipt")}
        >
          {receiptLabel}
        </button>
        <button
          type="button"
          role="tab"
          id={`${baseId}-tab-evidence`}
          aria-selected={tab === "evidence"}
          aria-controls={`${baseId}-panel-evidence`}
          className={`seg-item ${tab === "evidence" ? "on" : ""}`}
          onClick={() => setTab("evidence")}
        >
          {evidenceLabel}
        </button>
      </div>
      <div
        className="verify-split"
        role="tabpanel"
        id={`${baseId}-panel-${tab}`}
        aria-labelledby={`${baseId}-tab-${tab}`}
      >
        {tab === "receipt" ? receipt : evidence}
      </div>
    </>
  );
}
