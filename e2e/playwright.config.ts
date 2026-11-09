import { defineConfig, devices } from "@playwright/test";

// Inside the compose network the app is reachable as "proxy", but Secure cookies only work on
// https or on localhost. Mapping localhost to the containers keeps the real cookie settings on.
export const CHROME_ARGS = ["--host-resolver-rules=MAP localhost:8088 proxy:80,MAP localhost:8090 mock-pay:8000"];

export default defineConfig({
  testDir: "./tests",
  timeout: 120_000,
  workers: 1,
  reporter: "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://localhost:8088",
    launchOptions: { args: CHROME_ARGS },
  },
});
