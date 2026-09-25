import type { Metadata } from "next";
import Link from "next/link";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "@fontsource-variable/space-grotesk";
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
    <span className="chip">
      <span className={ok ? "dot dot-ok" : "dot dot-bad"} aria-hidden="true" />
      indexer {ok ? "live" : "down"}
    </span>
  );
}

/** Same protocol facts as before, as refined chips inside the header pill. */
function ProtocolStrip() {
  const chain = process.env.NEXT_PUBLIC_CHAIN ?? "arbitrum-sepolia";
  return (
    <div className="protocol-strip">
      <span className="chip">{chain}</span>
      <span className="chip">{contractShortform()}</span>
      <IndexerStatus />
    </div>
  );
}

/** Fixed decorative background: mesh gradients, blueprint grid, film grain. */
function BackgroundLayers() {
  return (
    <div className="bg-layers" aria-hidden="true">
      <div className="bg-mesh" />
      <div className="bg-grid" />
      <div className="bg-grain" />
    </div>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>
        <BackgroundLayers />
        <header className="site-header">
          <div className="header-pill">
            <Link href="/" className="wordmark">
              COVENANT
            </Link>
            <div className="header-right">
              <nav aria-label="Site">
                <Link href="/">Leaderboard</Link>
                <Link href="/verify">Verify a receipt</Link>
              </nav>
              <ProtocolStrip />
            </div>
          </div>
        </header>
        <main>{children}</main>
        <footer className="site-footer">
          <div className="footer-inner">
            <span>
              Covenant - non-custodial proof of performance on Arbitrum.
              Returns are computed from finalized checkpoints only.
            </span>
            <nav className="footer-nav" aria-label="Footer">
              <Link href="/">Leaderboard</Link>
              <Link href="/verify">Verify</Link>
            </nav>
          </div>
        </footer>
      </body>
    </html>
  );
}
