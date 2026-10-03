/**
 * Captures the Covenant demo footage as video (take 2, headed browser):
 * leaderboard, strategy 2 detail page (10.5 / 10.5 / +5.5), the receipt
 * verifier (VERIFIED + tamper REJECTED), and the Arbiscan pages. One
 * continuous 1920x1080 take -> submission-video/demo/raw-take.webm.
 */
import { chromium } from "playwright";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch({ headless: false, args: ["--window-size=1920,1080"] });
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  recordVideo: { dir: new URL("./", import.meta.url).pathname, size: { width: 1920, height: 1080 } },
});
const page = await context.newPage();

// ---- Act 1: the live leaderboard -----------------------------------------
await page.goto("http://localhost:3000", { waitUntil: "networkidle" });
await sleep(4000); // hero + entrance motion
await page.mouse.wheel(0, 750); // protocol pulse tiles
await sleep(2800);
await page.mouse.wheel(0, 750); // strategies table
await sleep(3200);

// ---- Act 2: strategy 2 detail page (formatted numbers) --------------------
await page.goto("http://localhost:3000/strategies/2", { waitUntil: "networkidle" });
await sleep(5000);
await page.mouse.wheel(0, 500);
await sleep(3000);

// ---- Act 3: the receipt verifier ------------------------------------------
await page.goto("http://localhost:3000/verify", { waitUntil: "networkidle" });
await sleep(1800);
await page.fill("#strategy-id", "2");
await page.fill("#epoch-index", "0");
await page.fill(
  "#receipt-hash",
  "0x9e95a8466db9fabc97ae212ee161023b957117e4a466b6385de001a199536cee",
);
await page.fill(
  "#merkle-proof",
  [
    "0xccdb3b878d11d8898151974815ee1219e5e8e511de7fa98e344b8ae424667d5d",
    "0x9da0dd22c4cc22eacb16cfed1ebf388b314d4a44cf963854a941deb67549f612",
    "0x271f531ef4a35ddf57682dc1f98177715278ae05a2f42b6d052fc340e3628715",
  ].join("\n"),
);
await sleep(900);
await page.click("button:has-text('Verify onchain')");
await page.locator(".verdict", { hasText: "VERIFIED" }).waitFor({ timeout: 45000 });
await sleep(5000); // linger on the green stamp
// tamper: one character of the receipt hash
await page.fill(
  "#receipt-hash",
  "0x9e95a8466db9fabc97ae212ee161023b957117e4a466b6385de001a199536ced",
);
await page.click("button:has-text('Verify onchain')");
await page.locator(".verdict", { hasText: "REJECTED" }).waitFor({ timeout: 45000 });
await sleep(4500); // linger on the rejection

// ---- Act 4: Arbiscan (headed to pass the bot check) ------------------------
const arbiscan = async (url, linger) => {
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  // Cloudflare interstitial may hold the page; give it up to 25s to clear
  for (let i = 0; i < 5; i++) {
    await sleep(5000);
    const title = await page.title();
    if (!/just a moment|attention required/i.test(title)) break;
  }
  await sleep(linger);
};

await arbiscan("https://sepolia.arbiscan.io/tx/0x253957bf13b0613fc0b8e411e0d668f6572b3c87568f882fa6db3c826b279298", 5000);
await page.mouse.wheel(0, 600);
await sleep(3000);
await arbiscan("https://sepolia.arbiscan.io/address/0x1de6ccb02f29308851a9f59c09845c6d348d16a4", 4500);
await page.mouse.wheel(0, 500);
await sleep(3000);
await arbiscan("https://sepolia.arbiscan.io/address/0x92613e84e3473f3886172947ecdff3627978fd18", 4500);

await browser.close();
console.log("take complete");
