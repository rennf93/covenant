import type { Metadata } from "next";
import Link from "next/link";
import { indexerUrl } from "../lib/api";
import "./globals.css";

export const metadata: Metadata = {
  title: "Covenant - onchain proof of performance",
  description:
    "Non-custodial, tamper-evident track records for trading strategies, settled in USDG on Arbitrum.",
};

/** Shortform of the deployed contract address, or "not deployed". */
function contractShortform(): string {
  const addr = process.env.NEXT_PUBLIC_CONTRACT_ADDRESS ?? "";
  if (!addr) return "not deployed";
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

/**
 * Real indexer health, checked server-side on every render: emerald dot when
 * the indexer responds, red when it does not.
 */
async function IndexerStatus() {
  let ok = false;
  try {
    const res = await fetch(`${indexerUrl()}/strategies`, {
      cache: "no-store",
      signal: AbortSignal.timeout(2500),
    });
    ok = res.ok;
  } catch {
    ok = false;
  }
  return (
    <span>
      <span className={ok ? "dot dot-ok" : "dot dot-bad"} />
      indexer
    </span>
  );
}

function ProtocolStrip() {
  const chain = process.env.NEXT_PUBLIC_CHAIN ?? "arbitrum-sepolia";
  return (
    <div className="protocol-strip">
      <span>{chain}</span>
      <span>{contractShortform()}</span>
      <IndexerStatus />
    </div>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <Link href="/" className="wordmark">
            COVENANT
          </Link>
          <div className="header-right">
            <nav>
              <Link href="/">Leaderboard</Link>
              <Link href="/verify">Verify a receipt</Link>
            </nav>
            <ProtocolStrip />
          </div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
