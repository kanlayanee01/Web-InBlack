// Runs Lighthouse (mobile) on the product page and enforces the thresholds from spec 9.3.
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import net from "node:net";

import { chromium } from "@playwright/test";

const URL = "http://localhost:8088/th/products/diamond-1000";
const REPORT = "/tmp/lighthouse.json";
const REQUIRED = { performance: 90, accessibility: 95, "best-practices": 95, seo: 100 };

// Lighthouse must see "localhost" for the page to count as a secure context, so that port is
// forwarded to the proxy container.
const forwarder = net.createServer((client) => {
  const upstream = net.connect(80, "proxy");
  client.pipe(upstream).pipe(client);
  client.on("error", () => upstream.destroy());
  upstream.on("error", () => client.destroy());
});
await new Promise((resolve) => forwarder.listen(8088, "127.0.0.1", resolve));

const exitCode = await new Promise((resolve) => {
  const child = spawn(
    "npx",
    [
      "lighthouse",
      URL,
      "--quiet",
      "--output=json",
      `--output-path=${REPORT}`,
      "--chrome-flags=--headless=new --no-sandbox",
      `--only-categories=${Object.keys(REQUIRED).join(",")}`,
    ],
    { stdio: "inherit", env: { ...process.env, CHROME_PATH: chromium.executablePath() } },
  );
  child.on("exit", resolve);
});
forwarder.close();
if (exitCode !== 0) process.exit(1);

const report = JSON.parse(readFileSync(REPORT, "utf-8"));
let failed = false;
for (const [category, minimum] of Object.entries(REQUIRED)) {
  const score = Math.round(report.categories[category].score * 100);
  const ok = score >= minimum;
  failed ||= !ok;
  console.log(`${ok ? "PASS" : "FAIL"} ${category}: ${score} (needs ${minimum})`);
  for (const ref of report.categories[category].auditRefs) {
    const audit = report.audits[ref.id];
    if (ref.weight > 0 && audit.score !== null && audit.score < 0.9) {
      console.log(`  - ${audit.id}: ${audit.score} ${audit.displayValue ?? ""}`);
    }
  }
}
process.exit(failed ? 1 : 0);
