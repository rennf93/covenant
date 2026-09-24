import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Proven: proof-of-performance for trading strategies",
  description:
    "Non-custodial, tamper-evident track records for trading strategies, settled in USDG on Arbitrum.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <Link href="/" className="brand">
            Proven
          </Link>
          <nav>
            <Link href="/">Leaderboard</Link>
            <Link href="/verify">Verify a receipt</Link>
          </nav>
        </header>
        <main>{children}</main>
        <footer>
          Non-custodial attestation layer. History is tamper-evident, not
          omniscient: challenge a pending epoch if you believe it is wrong.
        </footer>
      </body>
    </html>
  );
}
