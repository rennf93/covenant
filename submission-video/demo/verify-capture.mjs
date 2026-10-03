/**
 * Records ONLY the verify act with the verdict in frame: fill, click,
 * scroll so VERIFIED is centered, linger; tamper, REJECTED, linger.
 * Output: verify-take.webm (~30s).
 */
import { chromium } from "playwright";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  recordVideo: { dir: new URL("./", import.meta.url).pathname, size: { width: 1920, height: 1080 } },
});
const page = await context.newPage();

await page.goto("http://localhost:3000/verify", { waitUntil: "networkidle" });
await sleep(1500);
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
await sleep(800);
await page.click("button:has-text('Verify onchain')");
await page.locator(".verdict", { hasText: "VERIFIED" }).waitFor({ timeout: 45000 });
await page.mouse.wheel(0, 320); // bring the verdict stamp into center frame
await sleep(6000);
// tamper: one character of the receipt hash
await page.fill(
  "#receipt-hash",
  "0x9e95a8466db9fabc97ae212ee161023b957117e4a466b6385de001a199536ced",
);
await page.click("button:has-text('Verify onchain')");
await page.locator(".verdict", { hasText: "REJECTED" }).waitFor({ timeout: 45000 });
await page.mouse.wheel(0, 120);
await sleep(5500);

await browser.close();
console.log("verify take complete");
